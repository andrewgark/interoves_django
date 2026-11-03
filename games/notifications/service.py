"""Create, fold, list and read notifications."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from games.notifications.kinds import get_kind
from games.notifications.models import Notification, NotificationPrefs
from games.notifications.prefs import channels

logger = logging.getLogger('application')

INBOX_LIMIT = 50
GROUP_ICONS = {
    'friends': 'handshake',
    'support': 'lifebuoy',
    'offers': 'puzzle-piece',
    'games': 'flag-checkered',
    'club': 'crown-simple',
}


def _user_id(user) -> int:
    return int(getattr(user, 'pk', user))


def notify(user, kind_key: str, *, actor=None, **data) -> Notification | None:
    """Record one event for ``user`` and schedule its Telegram copy.

    Call it from domain code; it is safe inside a transaction (side effects
    run on commit) and never raises into the caller.
    """
    try:
        return _notify(_user_id(user), kind_key, actor_id=None if actor is None else _user_id(actor), data=data)
    except Exception:
        logger.exception('event=notification_failed kind=%s user=%s', kind_key, getattr(user, 'pk', user))
        return None


def _notify(user_id: int, kind_key: str, *, actor_id: int | None, data: dict) -> Notification | None:
    kind = get_kind(kind_key)
    if kind is None:
        raise ValueError('unknown notification kind: {}'.format(kind_key))
    if actor_id is not None and actor_id == user_id:
        return None
    site, telegram = channels(user_id, kind.group)
    if not site and not telegram:
        return None
    group_key = kind.full_group_key(data)
    now = timezone.now()
    with transaction.atomic():
        row = (
            Notification.objects.select_for_update()
            .filter(user_id=user_id, group_key=group_key, read_at__isnull=True, in_inbox=site)
            .order_by('-updated_at')
            .first()
        )
        rearm = telegram
        if row is None:
            row = Notification(
                user_id=user_id, kind=kind.key, group_key=group_key, actor_id=actor_id,
                payload=dict(data), in_inbox=site, updated_at=now,
            )
        else:
            if actor_id is not None and actor_id != row.actor_id:
                row.actor_count += 1
                row.actor_id = actor_id
            row.payload = {**row.payload, **data}
            row.updated_at = now
            rearm = telegram and (kind.telegram_on_merge or row.tg_state == Notification.TG_NONE)
        if rearm:
            from games.notifications.telegram import arm

            rearm = arm(row, kind, now=now)
        row.save()
    notification_id = row.pk
    transaction.on_commit(lambda: _after_commit(notification_id, push=site, schedule=rearm))
    return row


def _after_commit(notification_id: int, *, push: bool, schedule: bool) -> None:
    row = Notification.objects.filter(pk=notification_id).first()
    if row is None:
        return
    if push:
        _push_badge(row.user_id)
    if schedule:
        from games.notifications.telegram import schedule as schedule_telegram

        try:
            schedule_telegram(row)
        except Exception:
            # The minute sweep picks up a pending row that never reached the queue.
            logger.exception('event=notification_schedule_failed id=%s', notification_id)


def _push_badge(user_id: int) -> None:
    try:
        from games.realtime.broadcast import notify_user_after_commit

        notify_user_after_commit(user_id, {
            'type': 'track.event',
            'event': 'notification.new',
            'payload': {'badge': badge_count(user_id)},
        })
    except Exception:
        logger.exception('event=notification_push_failed user=%s', user_id)


def _unread(user_id: int):
    return Notification.objects.filter(user_id=user_id, in_inbox=True, read_at__isnull=True)


def badge_count(user_id: int) -> int:
    """Unread notifications that arrived after the bell was last opened."""
    qs = _unread(_user_id(user_id))
    seen_at = NotificationPrefs.objects.filter(user_id=_user_id(user_id)).values_list('seen_at', flat=True).first()
    if seen_at is not None:
        qs = qs.filter(updated_at__gt=seen_at)
    return qs.count()


def mark_seen(user) -> None:
    NotificationPrefs.objects.update_or_create(user_id=_user_id(user), defaults={'seen_at': timezone.now()})


def get_for_user(user, notification_id) -> Notification | None:
    return Notification.objects.filter(pk=notification_id, user_id=_user_id(user), in_inbox=True).first()


def mark_read(user, ids=None) -> int:
    """Mark the given notifications (or all of them) read."""
    qs = _unread(_user_id(user))
    if ids is not None:
        qs = qs.filter(pk__in=list(ids))
    return qs.update(read_at=timezone.now())


def mark_read_for_page(user, url_name: str) -> int:
    """Opening a page answers the notifications that point to it."""
    if not url_name:
        return 0
    from games.notifications.kinds import all_kinds

    kinds = [kind.key for kind in all_kinds() if url_name in kind.mark_read_on]
    if not kinds:
        return 0
    return _unread(_user_id(user)).filter(kind__in=kinds).update(read_at=timezone.now())


def render(notification: Notification) -> dict | None:
    """Site view of one notification, or None if it no longer applies."""
    kind = get_kind(notification.kind)
    if kind is None:
        return None
    try:
        if not kind.is_valid(notification):
            return None
        item = kind.render(notification)
    except Exception:
        logger.exception('event=notification_render_failed id=%s kind=%s', notification.pk, notification.kind)
        return None
    if item is None:
        return None
    return {
        'id': notification.pk,
        'kind': notification.kind,
        'group': kind.group,
        'icon': GROUP_ICONS.get(kind.group, 'bell'),
        'unread': notification.read_at is None,
        'at': notification.updated_at,
        **item,
    }


def inbox(user, *, limit: int = INBOX_LIMIT, group: str | None = None) -> list[dict]:
    qs = (
        Notification.objects.filter(user_id=_user_id(user), in_inbox=True)
        .select_related('actor', 'actor__profile')
        .order_by('-updated_at')
    )
    if group:
        from games.notifications.kinds import all_kinds

        qs = qs.filter(kind__in=[kind.key for kind in all_kinds() if kind.group == group])
    items = []
    for row in qs[:limit]:
        item = render(row)
        if item is not None:
            items.append(item)
    return items
