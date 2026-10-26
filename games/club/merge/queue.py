"""Durable queue execution for authenticated account merges."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import timedelta

import boto3
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from games.account_merge import AccountMergeError, merge_accounts
from games.models import AccountMerge, AccountMergeJob

logger = logging.getLogger('application')

EVENT_TTL_SECONDS = 24 * 60 * 60
MERGE_LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 5
RETRY_DELAY = timedelta(seconds=30)
EVENT_RETRY_AFTER = timedelta(minutes=5)
LEASE_HEARTBEAT_INTERVAL = 60
QUEUE_URL_ENV = 'IDENTITY_SQS_QUEUE_URL'


def _cache_key(job_id):
    return 'account-merge-event:{}'.format(job_id)


def _event_owned(job_id):
    return caches['track_revisions'].get(_cache_key(job_id)) is not None


def _mark_event(job_id):
    caches['track_revisions'].set(_cache_key(job_id), '1', timeout=EVENT_TTL_SECONDS)


def _clear_event(job_id):
    caches['track_revisions'].delete(_cache_key(job_id))


def serialize_account_merge_job(job):
    return {
        'id': str(job.id),
        'status': job.status,
        'attempt_count': job.attempt_count,
        'error': 'merge_failed' if job.status == AccountMergeJob.STATUS_FAILED else '',
        'created_at': job.created_at.isoformat() if job.created_at else '',
        'completed_at': job.completed_at.isoformat() if job.completed_at else '',
        'next_url': job.next_url or '',
    }


@transaction.atomic
def enqueue_account_merge(*, target_user, source_user, provider, provider_uid, next_url=''):
    """Create or reuse a short-lived durable merge request."""
    if target_user.pk == source_user.pk:
        raise AccountMergeError('Нельзя объединить профиль с самим собой.')

    User = get_user_model()
    ids = sorted((target_user.pk, source_user.pk))
    locked = User.objects.select_for_update().in_bulk(ids)
    target = locked.get(target_user.pk)
    source = locked.get(source_user.pk)
    if target is None or source is None or not source.is_active:
        raise AccountMergeError('Один из профилей больше не существует или уже объединён.')

    existing_merge = AccountMerge.objects.filter(
        target_user_id_snapshot=target.pk,
        source_user_id_snapshot=source.pk,
    ).first()
    if existing_merge is not None:
        job = AccountMergeJob.objects.filter(account_merge=existing_merge).first()
        if job is not None:
            return job
        raise AccountMergeError('Эти профили уже объединены.')

    try:
        with transaction.atomic():
            job = AccountMergeJob.objects.create(
                target_user=target,
                source_user=source,
                target_user_id_snapshot=target.pk,
                source_user_id_snapshot=source.pk,
                provider=str(provider or ''),
                provider_uid=str(provider_uid or ''),
                next_url=str(next_url or ''),
            )
        created = True
    except IntegrityError:
        job = AccountMergeJob.objects.get(
            target_user_id_snapshot=target.pk,
            source_user_id_snapshot=source.pk,
            provider=str(provider or ''),
            provider_uid=str(provider_uid or ''),
        )
        created = False
        if job.status == AccountMergeJob.STATUS_FAILED:
            job.status = AccountMergeJob.STATUS_PENDING
            job.last_error = ''
            job.claim_token = None
            job.claimed_until = None
            job.next_attempt_at = None
            job.attempt_count = 0
            job.save(update_fields=[
                'status', 'last_error', 'claim_token', 'claimed_until',
                'next_attempt_at', 'attempt_count', 'updated_at',
            ])
            created = True
    if created:
        schedule_account_merge_event(job.pk)
    return job


def publish_account_merge_event(job_id, *, delay_seconds=0):
    queue_url = (
        os.environ.get(QUEUE_URL_ENV)
        or os.environ.get('ANONYMOUS_MERGE_SQS_QUEUE_URL')
        or os.environ.get('WORKER_QUEUE_URL')
        or ''
    ).strip()
    if not queue_url:
        logger.error('account merge event skipped: queue url missing job=%s', job_id)
        return False
    delay = max(0, min(900, int(delay_seconds)))
    _mark_event(job_id)
    body = {
        'version': 1,
        'type': 'account.merge',
        'run_id': str(uuid.uuid4()),
        'scheduled_for': timezone.now().isoformat(),
        'dedupe_key': 'account.merge:{}'.format(job_id),
        'payload': {'job_id': str(job_id)},
    }
    try:
        client = boto3.client(
            'sqs',
            region_name=os.environ.get('AWS_REGION')
            or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )
        kwargs = {
            'QueueUrl': queue_url,
            'MessageBody': json.dumps(body, separators=(',', ':')),
        }
        if delay:
            kwargs['DelaySeconds'] = delay
        client.send_message(**kwargs)
    except Exception:
        _clear_event(job_id)
        logger.exception('account merge event failed job=%s', job_id)
        return False
    logger.info('account merge event published job=%s delay_seconds=%s', job_id, delay)
    return True


def schedule_account_merge_event(job_id, *, delay_seconds=0):
    transaction.on_commit(
        lambda: publish_account_merge_event(job_id, delay_seconds=delay_seconds),
    )


def publish_unmarked_account_merge_jobs(*, limit=5, now=None):
    now = now or timezone.now()
    jobs = AccountMergeJob.objects.filter(
        Q(status=AccountMergeJob.STATUS_PENDING)
        | Q(status=AccountMergeJob.STATUS_RUNNING, claimed_until__lte=now),
    ).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
    ).order_by('created_at')[:limit]
    published = 0
    for job in jobs:
        owned = _event_owned(job.pk)
        event_stale = (
            job.status == AccountMergeJob.STATUS_RUNNING
            or job.created_at <= now - EVENT_RETRY_AFTER
        )
        if owned and event_stale:
            _clear_event(job.pk)
            owned = False
        if not owned and publish_account_merge_event(job.pk):
            published += 1
    return published


@transaction.atomic
def _claim_job(job_id, *, now=None, worker='identity'):
    now = now or timezone.now()
    job = AccountMergeJob.objects.select_for_update().filter(pk=job_id).first()
    if job is None:
        return None, 'missing'
    if job.status == AccountMergeJob.STATUS_COMPLETED:
        return job, 'completed'
    if job.status == AccountMergeJob.STATUS_FAILED:
        return job, 'failed'
    if job.next_attempt_at and job.next_attempt_at > now:
        return job, 'deferred'
    if (
        job.status == AccountMergeJob.STATUS_RUNNING
        and job.claimed_until is not None
        and job.claimed_until > now
    ):
        return job, 'running'
    job.status = AccountMergeJob.STATUS_RUNNING
    job.claim_token = uuid.uuid4()
    job.claimed_until = now + MERGE_LEASE
    job.attempt_count += 1
    job.started_at = job.started_at or now
    job.next_attempt_at = None
    job.last_error = ''
    job.save(update_fields=[
        'status', 'claim_token', 'claimed_until', 'attempt_count',
        'started_at', 'next_attempt_at', 'last_error', 'updated_at',
    ])
    logger.info(
        'account merge claimed job=%s worker=%s attempt=%s',
        job.id, worker, job.attempt_count,
    )
    return job, 'claimed'


def renew_account_merge_lease(job_id, claim_token):
    """Extend an active lease without allowing a stale worker to revive it."""
    now = timezone.now()
    updated = AccountMergeJob.objects.filter(
        pk=job_id,
        status=AccountMergeJob.STATUS_RUNNING,
        claim_token=claim_token,
    ).update(
        claimed_until=now + MERGE_LEASE,
        updated_at=now,
    )
    return bool(updated)


class _LeaseHeartbeat:
    def __init__(self, job_id, claim_token):
        self.job_id = job_id
        self.claim_token = claim_token
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name='account-merge-lease',
            daemon=True,
        )

    def _run(self):
        from django.db import close_old_connections

        while not self.stop_event.wait(LEASE_HEARTBEAT_INTERVAL):
            close_old_connections()
            try:
                if not renew_account_merge_lease(self.job_id, self.claim_token):
                    return
            except Exception:
                logger.exception('account merge lease heartbeat failed job=%s', self.job_id)
            finally:
                close_old_connections()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop_event.set()
        self.thread.join(timeout=5)


def _mark_completed(job_id, merge, claim_token):
    now = timezone.now()
    updated = AccountMergeJob.objects.filter(
        pk=job_id,
        status=AccountMergeJob.STATUS_RUNNING,
        claim_token=claim_token,
    ).update(
        status=AccountMergeJob.STATUS_COMPLETED,
        account_merge=merge,
        claim_token=None,
        claimed_until=None,
        next_attempt_at=None,
        completed_at=now,
        updated_at=now,
    )
    return bool(updated)


def _mark_failed(job_id, error, claim_token):
    now = timezone.now()
    updated = AccountMergeJob.objects.filter(
        pk=job_id,
        status=AccountMergeJob.STATUS_RUNNING,
        claim_token=claim_token,
    ).update(
        status=AccountMergeJob.STATUS_FAILED,
        last_error=str(error)[:4000],
        claim_token=None,
        claimed_until=None,
        next_attempt_at=None,
        updated_at=now,
    )
    if updated:
        _clear_event(job_id)
    return bool(updated)


def _schedule_retry(job, error):
    now = timezone.now()
    if job.attempt_count >= MAX_ATTEMPTS:
        return 'failed' if _mark_failed(job.pk, error, job.claim_token) else 'stale'
    updated = AccountMergeJob.objects.filter(
        pk=job.pk,
        status=AccountMergeJob.STATUS_RUNNING,
        claim_token=job.claim_token,
    ).update(
        status=AccountMergeJob.STATUS_PENDING,
        last_error=str(error)[:4000],
        claim_token=None,
        claimed_until=None,
        next_attempt_at=now + RETRY_DELAY,
        updated_at=now,
    )
    if not updated:
        return 'stale'
    _clear_event(job.pk)
    schedule_account_merge_event(job.pk, delay_seconds=int(RETRY_DELAY.total_seconds()))
    return 'retry_scheduled'


def run_account_merge_job(job_id, *, worker='identity'):
    job, state = _claim_job(job_id, worker=worker)
    if state in {'missing', 'completed', 'failed', 'running', 'deferred'}:
        return {'status': state}

    existing = AccountMerge.objects.filter(
        target_user_id_snapshot=job.target_user_id_snapshot,
        source_user_id_snapshot=job.source_user_id_snapshot,
    ).first()
    if existing is not None:
        marked = _mark_completed(job.pk, existing, job.claim_token)
        return {
            'status': 'completed' if marked else 'stale',
            'merge_id': existing.pk,
        }

    User = get_user_model()
    target = User.objects.filter(pk=job.target_user_id_snapshot).first()
    source = User.objects.filter(pk=job.source_user_id_snapshot).first()
    if target is None or source is None:
        marked = _mark_failed(
            job.pk, 'Один из профилей больше не существует.', job.claim_token,
        )
        return {'status': 'failed' if marked else 'stale'}

    try:
        with _LeaseHeartbeat(job.pk, job.claim_token):
            merge = merge_accounts(
                target_user=target,
                source_user=source,
                provider=job.provider,
                provider_uid=job.provider_uid,
            )
    except AccountMergeError as exc:
        marked = _mark_failed(job.pk, exc, job.claim_token)
        return {'status': 'failed' if marked else 'stale'}
    except Exception as exc:
        logger.exception('account merge worker failed job=%s', job.pk)
        return {'status': _schedule_retry(job, exc)}

    marked = _mark_completed(job.pk, merge, job.claim_token)
    return {
        'status': 'completed' if marked else 'stale',
        'merge_id': merge.pk,
    }
