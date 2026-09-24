"""DB-backed dispatcher for Word Salad recheck transport intents."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import timedelta

import boto3
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from games.models import WordSaladRecheckItem, WordSaladRecheckOutbox

logger = logging.getLogger('application')
OUTBOX_RETRY_BASE = 30
OUTBOX_MAX_BACKOFF = 900
OUTBOX_CLAIM_TIMEOUT = timedelta(minutes=5)


class WordSaladTransportNotConfigured(RuntimeError):
    pass


class SQSWordSaladTransport:
    def __init__(self, *, queue_url=None, client=None):
        self.queue_url = queue_url or os.environ.get('WORD_SALAD_SQS_QUEUE_URL', '').strip()
        self.client = client

    def send(self, payload):
        if not self.queue_url:
            raise WordSaladTransportNotConfigured('WORD_SALAD_SQS_QUEUE_URL is not configured')
        client = self.client or boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        response = client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        )
        return response.get('MessageId', '')


class FakeWordSaladTransport:
    """Small test transport; intentionally never used by production discovery."""

    def __init__(self):
        self.messages = []

    def send(self, payload):
        self.messages.append(payload)
        return 'fake-{}'.format(len(self.messages))


def _payload(outbox):
    item = outbox.item
    job = item.job
    return {
        'version': 1,
        'operation': 'word_salad_recheck',
        'job_id': job.pk,
        'item_id': item.pk,
        # ``actor_id`` is the serialized actor identity, not the outbox/item
        # primary key.  The DB item remains authoritative for resolving it.
        'actor_id': item.actor_key,
        'task_revision': str(outbox.task_revision),
    }


def _claim_one(*, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        row = WordSaladRecheckOutbox.objects.filter(
            status=WordSaladRecheckOutbox.STATUS_PENDING,
        ).filter(
            next_attempt_at__isnull=True,
        ).select_for_update().select_related('item', 'item__job').order_by('id').first()
        if row is None:
            row = WordSaladRecheckOutbox.objects.filter(
                status=WordSaladRecheckOutbox.STATUS_PENDING,
                next_attempt_at__lte=now,
            ).select_for_update().select_related('item', 'item__job').order_by('id').first()
        if row is None:
            row = WordSaladRecheckOutbox.objects.filter(
                status=WordSaladRecheckOutbox.STATUS_SENDING,
            ).filter(
                Q(claimed_until__isnull=True) | Q(claimed_until__lte=now),
            ).select_for_update().select_related('item', 'item__job').order_by('id').first()
        if row is None:
            return None
        row.status = WordSaladRecheckOutbox.STATUS_SENDING
        row.claim_token = uuid.uuid4()
        row.claimed_until = now + OUTBOX_CLAIM_TIMEOUT
        row.attempts += 1
        row.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempts', 'updated_at'])
        return row


def _mark_sent(row, message_id=''):
    now = timezone.now()
    WordSaladRecheckOutbox.objects.filter(
        pk=row.pk, status=WordSaladRecheckOutbox.STATUS_SENDING, claim_token=row.claim_token,
    ).update(
        status=WordSaladRecheckOutbox.STATUS_SENT,
        sent_at=now,
        claimed_until=None,
        claim_token=None,
        last_error='',
        updated_at=now,
    )
    logger.info('word salad outbox sent outbox_id=%s item_id=%s message_id=%s', row.pk, row.item_id, message_id)


def _mark_failed(row, exc):
    now = timezone.now()
    delay = min(OUTBOX_MAX_BACKOFF, OUTBOX_RETRY_BASE * (2 ** max(0, row.attempts - 1)))
    WordSaladRecheckOutbox.objects.filter(
        pk=row.pk, status=WordSaladRecheckOutbox.STATUS_SENDING, claim_token=row.claim_token,
    ).update(
        status=WordSaladRecheckOutbox.STATUS_PENDING,
        next_attempt_at=now + timedelta(seconds=delay),
        claimed_until=None,
        claim_token=None,
        last_error='{}: {}'.format(exc.__class__.__name__, exc)[:2000],
        updated_at=now,
    )


def dispatch_word_salad_recheck_outbox(*, limit=10, transport=None):
    """Send at most ``limit`` intents; duplicate sends are expected and safe."""
    transport = transport or SQSWordSaladTransport()
    sent = failed = 0
    for _ in range(max(0, int(limit))):
        row = _claim_one()
        if row is None:
            break
        if row.item.status in (
            WordSaladRecheckItem.STATUS_COMPLETED,
            WordSaladRecheckItem.STATUS_SUPERSEDED,
        ):
            _mark_sent(row, message_id='already-terminal')
            sent += 1
            continue
        try:
            message_id = transport.send(_payload(row))
        except Exception as exc:
            failed += 1
            _mark_failed(row, exc)
            logger.exception('word salad outbox send failed outbox_id=%s item_id=%s', row.pk, row.item_id)
        else:
            sent += 1
            _mark_sent(row, message_id=message_id)
    return {'sent': sent, 'failed': failed}


def reconcile_word_salad_recheck_outbox(*, apply=False, now=None):
    """Audit and optionally repair durable DB-to-transport intents.

    This is deliberately a narrow repair command, not a second queue.  The
    item/job tables remain authoritative; repair only recreates a missing
    intent or makes an interrupted send eligible for another delivery.
    """
    now = now or timezone.now()
    findings = []

    items = WordSaladRecheckItem.objects.select_related('job').order_by('id')
    for item in items.iterator():
        outbox = WordSaladRecheckOutbox.objects.filter(
            item_id=item.pk, task_revision=item.job.task_revision,
        ).first()
        if outbox is None and item.status in (
            WordSaladRecheckItem.STATUS_PENDING,
            WordSaladRecheckItem.STATUS_RUNNING,
        ):
            finding = {'kind': 'missing_outbox', 'item_id': item.pk, 'job_id': item.job_id}
            if apply:
                with transaction.atomic():
                    WordSaladRecheckOutbox.objects.get_or_create(
                        item_id=item.pk,
                        task_revision=item.job.task_revision,
                    )
                finding['repaired'] = True
            findings.append(finding)
            continue
        if outbox is None:
            continue

        if outbox.status == WordSaladRecheckOutbox.STATUS_SENDING and (
            outbox.claimed_until is None or outbox.claimed_until <= now
        ):
            finding = {'kind': 'stale_sending', 'outbox_id': outbox.pk, 'item_id': item.pk}
            if apply:
                updated = WordSaladRecheckOutbox.objects.filter(
                    pk=outbox.pk,
                    status=WordSaladRecheckOutbox.STATUS_SENDING,
                ).filter(
                    Q(claimed_until__isnull=True) | Q(claimed_until__lte=now),
                ).update(
                    status=WordSaladRecheckOutbox.STATUS_PENDING,
                    claim_token=None,
                    claimed_until=None,
                    next_attempt_at=now,
                    updated_at=now,
                )
                finding['repaired'] = bool(updated)
            findings.append(finding)

        if item.status in (
            WordSaladRecheckItem.STATUS_COMPLETED,
            WordSaladRecheckItem.STATUS_SUPERSEDED,
        ) and outbox.status in (
            WordSaladRecheckOutbox.STATUS_PENDING,
            WordSaladRecheckOutbox.STATUS_SENDING,
        ):
            finding = {'kind': 'terminal_item_unsent', 'outbox_id': outbox.pk, 'item_id': item.pk}
            if apply:
                WordSaladRecheckOutbox.objects.filter(pk=outbox.pk).update(
                    status=WordSaladRecheckOutbox.STATUS_SENT,
                    sent_at=outbox.sent_at or now,
                    claim_token=None,
                    claimed_until=None,
                    updated_at=now,
                )
                finding['repaired'] = True
            findings.append(finding)

    return findings
