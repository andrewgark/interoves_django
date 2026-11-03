"""Telegram copies of notifications, delivered by the integrations worker.

The web process only marks a row pending and enqueues it, delayed by the
kind's ``telegram_delay`` when that fits in SQS (15 minutes). Rows deferred
past quiet hours, or whose message got lost, are picked up by the minute
sweep that runs inside the announcements tick. Either path claims the row
with a conditional update, so a duplicate message never sends twice.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from games.notifications.kinds import get_kind
from games.notifications.models import Notification, NotificationPrefs
from games.notifications.prefs import channels, outside_quiet_hours, prefs_for, user_timezone

logger = logging.getLogger('application')

NOTIFICATION_PAYLOAD_KIND = 'notification'
# SQS DelaySeconds ceiling.
MAX_QUEUE_DELAY = timedelta(minutes=15)
# A row enqueued this long ago and still pending lost its message.
LOST_AFTER = timedelta(minutes=10)
# Past this, a Telegram copy is noise rather than news.
STALE_AFTER = timedelta(hours=6)
SWEEP_LIMIT = 50
# A queue message may arrive a little before its due time.
EARLY_SLACK = timedelta(seconds=30)
RETENTION_READ = timedelta(days=90)
RETENTION_UNREAD = timedelta(days=180)


def _profile(user_id):
    from games.models import Profile

    return Profile.objects.filter(user_id=user_id).first()


def _linked(profile) -> bool:
    return bool(profile is not None and profile.telegram_verified and profile.telegram_user_id)


def arm(row: Notification, kind, *, now) -> bool:
    """Mark ``row`` pending for Telegram. Returns False if it cannot go there."""
    profile = _profile(row.user_id)
    if not _linked(profile):
        return False
    due = now + kind.telegram_delay
    if not kind.urgent:
        due = outside_quiet_hours(due, prefs_for(row.user_id), user_timezone(profile))
    row.tg_state = Notification.TG_PENDING
    row.tg_due_at = due
    row.tg_enqueued_at = None
    row.tg_reason = ''
    return True


def schedule(row: Notification, *, now=None) -> bool:
    """Enqueue a pending row whose due time fits in the queue delay."""
    from games.ops.telegram.bot.alerts import enqueue_integrations_message
    from games.ops.workers.messages import TELEGRAM_USER_NOTICE

    now = now or timezone.now()
    if row.tg_state != Notification.TG_PENDING or row.tg_due_at is None:
        return False
    delay = max(timedelta(0), row.tg_due_at - now)
    if delay > MAX_QUEUE_DELAY:
        return False  # the sweep sends it when quiet hours end
    enqueued = enqueue_integrations_message(
        message_type=TELEGRAM_USER_NOTICE,
        payload={'kind': NOTIFICATION_PAYLOAD_KIND, 'notification_id': row.pk},
        dedupe_key='notification:{}:{}'.format(row.pk, int(row.tg_due_at.timestamp())),
        label='notification_{}'.format(row.kind),
        delay_seconds=int(delay.total_seconds()),
    )
    if enqueued:
        Notification.objects.filter(pk=row.pk, tg_state=Notification.TG_PENDING).update(tg_enqueued_at=now)
    return enqueued


def _finish(row_id, state, reason, **extra) -> dict:
    Notification.objects.filter(pk=row_id).update(tg_state=state, tg_reason=reason, **extra)
    sent = state == Notification.TG_SENT
    return {'sent': sent, 'reason': '' if sent else reason}


def deliver(notification_id: int, *, now=None) -> dict:
    """Send one pending notification to Telegram if it still makes sense."""
    from games.ops.telegram.transport.api import SEND_BLOCKED, SEND_OK, send_message_status

    now = now or timezone.now()
    claimed = Notification.objects.filter(
        pk=notification_id, tg_state=Notification.TG_PENDING, tg_due_at__lte=now + EARLY_SLACK,
    ).update(tg_state=Notification.TG_SENDING)
    if not claimed:
        return {'sent': False, 'reason': 'not_pending'}
    row = Notification.objects.select_related('user').get(pk=notification_id)
    kind = get_kind(row.kind)
    if kind is None:
        return _finish(row.pk, Notification.TG_SKIPPED, 'unknown_kind')
    if row.tg_due_at is not None and now - row.tg_due_at > STALE_AFTER:
        return _finish(row.pk, Notification.TG_SKIPPED, 'stale')
    if not row.user.is_active:
        return _finish(row.pk, Notification.TG_SKIPPED, 'inactive')
    profile = _profile(row.user_id)
    if not _linked(profile):
        return _finish(row.pk, Notification.TG_SKIPPED, 'no_telegram')
    if row.in_inbox and row.read_at is not None:
        return _finish(row.pk, Notification.TG_SKIPPED, 'read')
    if not channels(row.user_id, kind.group)[1]:
        return _finish(row.pk, Notification.TG_SKIPPED, 'disabled')
    if not kind.is_valid(row):
        return _finish(row.pk, Notification.TG_SKIPPED, 'not_valid')
    if kind.telegram_cooldown is not None and Notification.objects.filter(
        user_id=row.user_id, group_key=row.group_key, tg_sent_at__gte=now - kind.telegram_cooldown,
    ).exclude(pk=row.pk).exists():
        return _finish(row.pk, Notification.TG_SKIPPED, 'cooldown')
    prefs = prefs_for(row.user_id)
    if not kind.urgent:
        # Quiet hours may have changed since the row was armed.
        wake = outside_quiet_hours(now, prefs, user_timezone(profile))
        if wake > now:
            Notification.objects.filter(pk=row.pk).update(
                tg_state=Notification.TG_PENDING, tg_due_at=wake, tg_enqueued_at=None,
            )
            return {'sent': False, 'reason': 'deferred'}
    message = kind.render_telegram(row)
    if not message:
        return _finish(row.pk, Notification.TG_SKIPPED, 'no_content')
    status = send_message_status(
        profile.telegram_user_id, message['text'], reply_markup=message.get('reply_markup'),
    )
    if status == SEND_OK:
        if prefs.telegram_blocked_at is not None:
            NotificationPrefs.objects.filter(user_id=row.user_id).update(telegram_blocked_at=None)
        return _finish(row.pk, Notification.TG_SENT, 'sent', tg_sent_at=now)
    if status == SEND_BLOCKED:
        NotificationPrefs.objects.update_or_create(user_id=row.user_id, defaults={'telegram_blocked_at': now})
        return _finish(row.pk, Notification.TG_FAILED, 'blocked')
    return _finish(row.pk, Notification.TG_FAILED, 'delivery_failed')


def run_worker_notice(payload: dict) -> dict:
    try:
        notification_id = int(payload.get('notification_id'))
    except (TypeError, ValueError):
        return {'sent': False, 'reason': 'bad_payload'}
    return deliver(notification_id)


def sweep(*, now=None) -> dict:
    """Send deferred rows that are due and rows whose queue message got lost."""
    from django.db.models import Q

    now = now or timezone.now()
    due = (
        Notification.objects.filter(tg_state=Notification.TG_PENDING, tg_due_at__lte=now)
        .filter(Q(tg_enqueued_at__isnull=True) | Q(tg_enqueued_at__lte=now - LOST_AFTER))
        .order_by('tg_due_at')
        .values_list('pk', flat=True)[:SWEEP_LIMIT]
    )
    sent = 0
    skipped = 0
    for notification_id in list(due):
        try:
            result = deliver(notification_id, now=now)
        except Exception:
            logger.exception('event=notification_sweep_failed id=%s', notification_id)
            Notification.objects.filter(pk=notification_id, tg_state=Notification.TG_SENDING).update(
                tg_state=Notification.TG_FAILED, tg_reason='error',
            )
            continue
        if result['sent']:
            sent += 1
        else:
            skipped += 1
    purged = purge_old(now=now) if now.minute == 7 else 0
    return {'sent': sent, 'skipped': skipped, 'purged': purged}


def purge_old(*, now=None) -> int:
    now = now or timezone.now()
    read, _ = Notification.objects.filter(read_at__lt=now - RETENTION_READ).delete()
    unread, _ = Notification.objects.filter(updated_at__lt=now - RETENTION_UNREAD).delete()
    return read + unread
