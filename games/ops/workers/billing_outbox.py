"""Retryable dispatch for transactional Club payment admin alerts."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from games.models import ClubPaymentAdminAlertOutbox

logger = logging.getLogger('application')
CLAIM_TIMEOUT = timedelta(minutes=2)
RETRY_BASE_SECONDS = 30
MAX_RETRY_SECONDS = 900


def enqueue_club_payment_admin_alert(payment, alert_type, *, reason=''):
    """Persist an admin-alert intent in the caller's payment transaction."""
    row, created = ClubPaymentAdminAlertOutbox.objects.get_or_create(
        payment=payment,
        alert_type=alert_type,
        defaults={'reason': str(reason or '')[:255]},
    )
    logger.info(
        'club_payment_admin_alert_outbox_recorded outbox_id=%s payment_id=%s '
        'alert=%s created=%s',
        row.pk, payment.pk, alert_type, created,
    )
    return row


def _claim(*, limit, now):
    claimed = []
    with transaction.atomic():
        eligible = (
            Q(status=ClubPaymentAdminAlertOutbox.STATUS_PENDING)
            & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        ) | (
            Q(status=ClubPaymentAdminAlertOutbox.STATUS_SENDING)
            & (Q(claimed_until__isnull=True) | Q(claimed_until__lte=now))
        )
        rows = ClubPaymentAdminAlertOutbox.objects.filter(eligible).order_by('id')
        if connection.features.has_select_for_update:
            rows = rows.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked,
            )
        for row in rows[:limit]:
            token = uuid.uuid4()
            row.status = ClubPaymentAdminAlertOutbox.STATUS_SENDING
            row.claim_token = token
            row.claimed_until = now + CLAIM_TIMEOUT
            row.attempts += 1
            row.save(update_fields=[
                'status', 'claim_token', 'claimed_until', 'attempts', 'updated_at',
            ])
            row.claim_token = token
            claimed.append(row)
    return claimed


def _publish(row):
    payment = row.payment
    if row.alert_type == ClubPaymentAdminAlertOutbox.ALERT_SUCCEEDED:
        from games.ops.telegram.bot.notify import notify_admin_club_subscription

        return notify_admin_club_subscription(
            payment.club_subscription_id,
            'payment.succeeded',
            payment_kind=payment.kind,
            payment_id=payment.pk,
            delivery='queue',
        )

    from games.ops.telegram.bot.notify import notify_admin_club_renewal_failed

    reason = row.reason or payment.cancellation_reason
    if row.alert_type == ClubPaymentAdminAlertOutbox.ALERT_UNKNOWN and not reason:
        reason = 'Неизвестный результат отправки платежа.'
    return notify_admin_club_renewal_failed(
        payment.club_subscription_id,
        payment_id=payment.pk,
        reason=reason,
        delivery='queue',
    )


def _mark_sent(row, *, now):
    return ClubPaymentAdminAlertOutbox.objects.filter(
        pk=row.pk,
        status=ClubPaymentAdminAlertOutbox.STATUS_SENDING,
        claim_token=row.claim_token,
    ).update(
        status=ClubPaymentAdminAlertOutbox.STATUS_SENT,
        sent_at=now,
        claimed_until=None,
        claim_token=None,
        last_error='',
        updated_at=now,
    )


def _mark_failed(row, *, now, error):
    delay = min(MAX_RETRY_SECONDS, RETRY_BASE_SECONDS * 2 ** max(0, row.attempts - 1))
    return ClubPaymentAdminAlertOutbox.objects.filter(
        pk=row.pk,
        status=ClubPaymentAdminAlertOutbox.STATUS_SENDING,
        claim_token=row.claim_token,
    ).update(
        status=ClubPaymentAdminAlertOutbox.STATUS_PENDING,
        next_attempt_at=now + timedelta(seconds=delay),
        claimed_until=None,
        claim_token=None,
        last_error=str(error)[:2000],
        updated_at=now,
    )


def dispatch_club_payment_admin_alert_outbox(*, limit=10, now=None):
    """Queue due admin alerts, retrying failures with bounded backoff."""
    now = now or timezone.now()
    rows = _claim(limit=max(1, min(int(limit), 100)), now=now)
    sent = failed = 0
    for row in rows:
        try:
            queued = _publish(row)
        except Exception as exc:
            queued = False
            error = '{}: {}'.format(exc.__class__.__name__, exc)
            logger.exception('club_payment_admin_alert_dispatch_error outbox_id=%s', row.pk)
        else:
            error = 'enqueue_returned_false'
        finished_at = timezone.now()
        if queued and _mark_sent(row, now=finished_at):
            sent += 1
        elif not queued and _mark_failed(row, now=finished_at, error=error):
            failed += 1
    if rows:
        logger.info(
            'club_payment_admin_alert_outbox_dispatch claimed=%s sent=%s failed=%s',
            len(rows), sent, failed,
        )
    return {'claimed': len(rows), 'sent': sent, 'failed': failed}
