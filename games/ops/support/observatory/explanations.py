"""Deterministic explanations for durable recheck state."""

from django.utils import timezone

from games.models import (
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)


def _is_expired(claimed_until, now):
    return claimed_until is not None and claimed_until <= now


def explain_outbox_state(outbox, *, now=None):
    now = now or timezone.now()
    if outbox.status == WordSaladRecheckOutbox.STATUS_PENDING:
        if outbox.next_attempt_at and outbox.next_attempt_at > now:
            return {
                'code': 'outbox_retry_scheduled',
                'label': 'Повторная отправка запланирована',
                'details': outbox.next_attempt_at.isoformat(),
            }
        return {
            'code': 'waiting_for_dispatch',
            'label': 'Ожидает отправки dispatcher',
            'details': None,
        }
    if outbox.status == WordSaladRecheckOutbox.STATUS_SENDING:
        if _is_expired(outbox.claimed_until, now):
            return {
                'code': 'outbox_claim_expired',
                'label': 'Claim dispatcher истёк',
                'details': None,
            }
        return {
            'code': 'outbox_dispatching',
            'label': 'Отправляется в SQS',
            'details': None,
        }
    if outbox.status == WordSaladRecheckOutbox.STATUS_SENT:
        return {'code': 'sent_to_transport', 'label': 'Отправлено в SQS', 'details': None}
    if outbox.status == WordSaladRecheckOutbox.STATUS_CANCELLED:
        return {'code': 'outbox_cancelled', 'label': 'Transport intent отменён', 'details': None}
    return {'code': 'unknown', 'label': 'Неизвестное состояние', 'details': None}


def explain_item_state(item, *, outbox=None, now=None):
    now = now or timezone.now()
    if item.status == WordSaladRecheckItem.STATUS_COMPLETED:
        return {'code': 'completed', 'label': 'Завершено', 'details': None}
    if item.status == WordSaladRecheckItem.STATUS_SUPERSEDED:
        return {'code': 'superseded', 'label': 'Заменено новой replay-работой', 'details': None}
    if item.status == WordSaladRecheckItem.STATUS_FAILED:
        return {
            'code': 'failed_after_retries',
            'label': 'Ошибка после исчерпания попыток',
            'details': item.last_error or None,
        }
    if item.status == WordSaladRecheckItem.STATUS_RUNNING:
        if _is_expired(item.claimed_until, now):
            return {'code': 'item_lease_expired', 'label': 'Lease item истёк', 'details': None}
        return {'code': 'processing', 'label': 'Обрабатывается worker', 'details': None}
    if item.next_attempt_at and item.next_attempt_at > now:
        return {
            'code': 'retry_scheduled',
            'label': 'Повторная попытка запланирована',
            'details': item.next_attempt_at.isoformat(),
        }
    if outbox is not None:
        outbox_explanation = explain_outbox_state(outbox, now=now)
        if outbox.status == WordSaladRecheckOutbox.STATUS_PENDING:
            return outbox_explanation
        if outbox.status == WordSaladRecheckOutbox.STATUS_SENDING:
            return outbox_explanation
        if outbox.status == WordSaladRecheckOutbox.STATUS_SENT:
            return {
                'code': 'waiting_for_worker',
                'label': 'Ожидает worker после отправки в SQS',
                'details': outbox.sent_at.isoformat() if outbox.sent_at else None,
            }
    return {'code': 'pending_without_outbox', 'label': 'Ожидает, outbox не найден', 'details': None}


def explain_job_state(job, *, open_items=None, outboxes=None, now=None):
    now = now or timezone.now()
    if job.status == WordSaladRecheckJob.STATUS_COMPLETED:
        return {'code': 'completed', 'label': 'Завершено', 'details': None}
    if job.status == WordSaladRecheckJob.STATUS_FAILED:
        return {
            'code': 'failed_after_retries',
            'label': 'Ошибка после исчерпания попыток',
            'details': job.last_error or None,
        }
    if job.status == WordSaladRecheckJob.STATUS_SUPERSEDED:
        return {'code': 'superseded', 'label': 'Заменено новой replay-работой', 'details': None}
    if job.status == WordSaladRecheckJob.STATUS_RUNNING:
        if _is_expired(job.claimed_until, now):
            return {'code': 'job_lease_expired', 'label': 'Lease job истёк', 'details': None}
        return {'code': 'processing', 'label': 'Обрабатывается worker', 'details': None}
    if job.next_attempt_at and job.next_attempt_at > now:
        return {
            'code': 'retry_scheduled',
            'label': 'Повторная попытка запланирована',
            'details': job.next_attempt_at.isoformat(),
        }
    if open_items is not None and not open_items:
        return {'code': 'no_open_items', 'label': 'Нет открытых items', 'details': None}
    if outboxes is not None:
        if any(row.status == WordSaladRecheckOutbox.STATUS_PENDING for row in outboxes):
            return {'code': 'waiting_for_dispatch', 'label': 'Ожидает отправки dispatcher', 'details': None}
        if any(row.status == WordSaladRecheckOutbox.STATUS_SENDING for row in outboxes):
            return {'code': 'outbox_dispatching', 'label': 'Outbox отправляется в SQS', 'details': None}
        if open_items and all(row.status == WordSaladRecheckOutbox.STATUS_SENT for row in outboxes):
            return {'code': 'waiting_for_worker', 'label': 'Ожидает worker', 'details': None}
    return {'code': 'pending', 'label': 'Ожидает обработки', 'details': None}
