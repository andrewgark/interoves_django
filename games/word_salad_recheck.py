"""Durable actor-by-actor queue for Word Salad rechecks."""

from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from games.models import (
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)
from games.recheck import _resolve_word_salad_actor, _word_salad_actor_keys, recheck_word_salad_actor

logger = logging.getLogger('application')
JOB_LEASE = timedelta(minutes=10)
ITEM_LEASE = timedelta(minutes=10)
RETRY_BASE = 30
MAX_ITEM_ATTEMPTS = 5


def _actor_key(actor):
    return json.dumps(list(actor), ensure_ascii=False, separators=(',', ':'))


def serialize_job(job):
    return {
        'id': job.pk, 'task_id': job.task_id, 'game_id': job.game_id,
        'status': job.status, 'total_actors': job.total_actors,
        'completed_actors': job.completed_actors,
        'credited_attempts': job.credited_attempts,
        'attempt_count': job.attempt_count, 'progress': job.progress,
        'last_error': job.last_error,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'updated_at': job.updated_at.isoformat() if job.updated_at else None,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
    }


@transaction.atomic
def enqueue_word_salad_recheck(*, task, game):
    """Snapshot actors and fence older jobs for this task/game."""
    task = type(task).objects.select_for_update().get(pk=task.pk)
    actors = sorted(
        _word_salad_actor_keys(task, game),
        key=lambda item: (item[0] or '', item[1] or 0, item[2] or '', item[3] or 0),
    )
    active = WordSaladRecheckJob.objects.select_for_update().filter(
        task=task, game=game,
        status__in=(WordSaladRecheckJob.STATUS_PENDING, WordSaladRecheckJob.STATUS_RUNNING),
    )
    now = timezone.now()
    active.update(status=WordSaladRecheckJob.STATUS_SUPERSEDED,
                  claim_token=None, claimed_until=None, updated_at=now)
    job = WordSaladRecheckJob.objects.create(
        task=task, game=game, task_revision=task.attempt_revision,
        status=WordSaladRecheckJob.STATUS_PENDING, total_actors=len(actors),
        next_attempt_at=now,
    )
    items = [
        WordSaladRecheckItem(
            job=job, actor_key=_actor_key(actor), team_id=actor[0], user_id=actor[1],
            anon_key=actor[2], replay_slot_id=actor[3], next_attempt_at=now,
        ) for actor in actors
    ]
    WordSaladRecheckItem.objects.bulk_create(items)
    WordSaladRecheckOutbox.objects.bulk_create([
        WordSaladRecheckOutbox(item=item, task_revision=job.task_revision)
        for item in WordSaladRecheckItem.objects.filter(job=job).only('id')
    ])
    return job


@transaction.atomic
def retry_word_salad_recheck(job_id):
    job = WordSaladRecheckJob.objects.select_for_update().get(pk=job_id)
    if job.status != WordSaladRecheckJob.STATUS_FAILED:
        return job
    if job.task.attempt_revision != job.task_revision:
        return job
    now = timezone.now()
    job.items.filter(status=WordSaladRecheckItem.STATUS_FAILED).update(
        status=WordSaladRecheckItem.STATUS_PENDING,
        attempt_count=0,
        last_error='',
        claim_token=None,
        claimed_until=None,
        next_attempt_at=now,
        completed_at=None,
        updated_at=now,
    )
    job.status = WordSaladRecheckJob.STATUS_PENDING
    job.last_error = ''
    job.claim_token = None
    job.claimed_until = None
    job.next_attempt_at = now
    job.completed_at = None
    job.save(update_fields=['status', 'last_error', 'claim_token', 'claimed_until', 'next_attempt_at', 'completed_at', 'updated_at'])
    return job


def _claim_next(now=None, worker='cron'):
    now = now or timezone.now()
    with transaction.atomic():
        jobs = WordSaladRecheckJob.objects.filter(
            Q(status=WordSaladRecheckJob.STATUS_PENDING)
            | Q(status=WordSaladRecheckJob.STATUS_RUNNING, claimed_until__lte=now),
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        ).order_by('created_at')
        job = jobs.select_for_update().first()
        if job is None:
            return None, None, None
        token = uuid.uuid4()
        job.status = WordSaladRecheckJob.STATUS_RUNNING
        job.claim_token = token
        job.claimed_until = now + JOB_LEASE
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.next_attempt_at = None
        job.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempt_count', 'started_at', 'next_attempt_at', 'updated_at'])
        item = WordSaladRecheckItem.objects.filter(
            Q(status=WordSaladRecheckItem.STATUS_PENDING)
            | Q(status=WordSaladRecheckItem.STATUS_RUNNING, claimed_until__lte=now),
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
            job=job,
        ).order_by('id').select_for_update().first()
        if item is None:
            remaining = WordSaladRecheckItem.objects.filter(job=job).exclude(
                status=WordSaladRecheckItem.STATUS_COMPLETED,
            ).order_by('next_attempt_at').first()
            failed = WordSaladRecheckItem.objects.filter(
                job=job, status=WordSaladRecheckItem.STATUS_FAILED,
            ).exists()
            job.status = (
                WordSaladRecheckJob.STATUS_FAILED if failed
                else WordSaladRecheckJob.STATUS_COMPLETED if remaining is None
                else WordSaladRecheckJob.STATUS_PENDING
            )
            job.completed_at = now if job.status in (
                WordSaladRecheckJob.STATUS_FAILED, WordSaladRecheckJob.STATUS_COMPLETED,
            ) else None
            job.next_attempt_at = None if job.status != WordSaladRecheckJob.STATUS_PENDING else remaining.next_attempt_at
            job.claim_token = None
            job.claimed_until = None
            job.save(update_fields=['status', 'completed_at', 'next_attempt_at', 'claim_token', 'claimed_until', 'updated_at'])
            return job, None, None
        item_token = uuid.uuid4()
        item.status = WordSaladRecheckItem.STATUS_RUNNING
        item.claim_token = item_token
        item.claimed_until = now + ITEM_LEASE
        item.attempt_count += 1
        item.started_at = item.started_at or now
        item.next_attempt_at = None
        item.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempt_count', 'started_at', 'next_attempt_at', 'updated_at'])
    logger.info('word salad recheck claimed job=%s item=%s worker=%s host=%s', job.pk, item.pk, worker, socket.gethostname())
    return job, item, item_token


def _record_item_failure(job, item, token, exc):
    now = timezone.now()
    message = '{}: {}'.format(exc.__class__.__name__, exc)[:2000]
    exhausted = item.attempt_count >= MAX_ITEM_ATTEMPTS
    WordSaladRecheckItem.objects.filter(pk=item.pk, claim_token=token).update(
        status=WordSaladRecheckItem.STATUS_FAILED if exhausted else WordSaladRecheckItem.STATUS_PENDING,
        last_error=message,
        next_attempt_at=None if exhausted else now + timedelta(seconds=RETRY_BASE * (2 ** max(0, item.attempt_count - 1))),
        claimed_until=None, claim_token=None, updated_at=now,
    )
    WordSaladRecheckJob.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
        status=WordSaladRecheckJob.STATUS_FAILED if exhausted else WordSaladRecheckJob.STATUS_PENDING,
        last_error=message,
        next_attempt_at=None if exhausted else now + timedelta(seconds=RETRY_BASE * (2 ** max(0, item.attempt_count - 1))),
        claimed_until=None, claim_token=None, updated_at=now,
    )


def _schedule_actor_notification(task, actor, game):
    """Publish realtime state only after replay and queue state commit."""
    from games.views.track import track_actor_task_change

    def notify():
        try:
            track_actor_task_change(
                task,
                team=actor.get('team'),
                user=actor.get('user'),
                anon_key=actor.get('anon_key'),
                game=game,
                reason='task.word_salad_rechecked',
            )
        except Exception:
            # Realtime delivery is derived state; it must never turn a
            # committed replay into a queue retry.
            logger.exception('word salad recheck notification failed task=%s', task.pk)

    transaction.on_commit(
        notify
    )


def _claim_specific_item(*, job_id, item_id, worker='worker', now=None):
    """Claim the item named by a transport message, preserving DB fencing."""
    now = now or timezone.now()
    with transaction.atomic():
        item = WordSaladRecheckItem.objects.select_for_update().select_related('job', 'job__task', 'job__game').get(
            pk=item_id, job_id=job_id,
        )
        if item.status == WordSaladRecheckItem.STATUS_COMPLETED:
            return 'completed', item.job, item, None
        if item.status == WordSaladRecheckItem.STATUS_SUPERSEDED:
            return 'superseded', item.job, item, None
        if item.status == WordSaladRecheckItem.STATUS_FAILED:
            return 'failed', item.job, item, None
        if item.status == WordSaladRecheckItem.STATUS_RUNNING and item.claimed_until and item.claimed_until > now:
            return 'lease_conflict', item.job, item, None

        job = item.job
        if job.status == WordSaladRecheckJob.STATUS_SUPERSEDED or job.task.attempt_revision != job.task_revision:
            item.status = WordSaladRecheckItem.STATUS_SUPERSEDED
            item.claim_token = None
            item.claimed_until = None
            item.updated_at = now
            item.save(update_fields=['status', 'claim_token', 'claimed_until', 'updated_at'])
            return 'superseded', job, item, None
        if job.status not in (WordSaladRecheckJob.STATUS_PENDING, WordSaladRecheckJob.STATUS_RUNNING):
            return 'not_due', job, item, None

        job_token = uuid.uuid4()
        item_token = uuid.uuid4()
        job.status = WordSaladRecheckJob.STATUS_RUNNING
        job.claim_token = job_token
        job.claimed_until = now + JOB_LEASE
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.next_attempt_at = None
        job.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempt_count', 'started_at', 'next_attempt_at', 'updated_at'])
        item.status = WordSaladRecheckItem.STATUS_RUNNING
        item.claim_token = item_token
        item.claimed_until = now + ITEM_LEASE
        item.attempt_count += 1
        item.started_at = item.started_at or now
        item.next_attempt_at = None
        item.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempt_count', 'started_at', 'next_attempt_at', 'updated_at'])
    logger.info('word salad recheck claimed job=%s item=%s worker=%s host=%s', job.pk, item.pk, worker, socket.gethostname())
    return 'claimed', job, item, item_token


def _process_claimed_item(job, item, item_token):
    started = time.perf_counter()
    logger.info(
        'word_salad_task_started job_id=%s item_id=%s task_revision=%s',
        job.pk, item.pk, job.task_revision,
    )
    try:
        task = job.task
        actor = _resolve_word_salad_actor(item.team_id, item.user_id, item.anon_key, item.replay_slot_id)
        credited = 0
        with transaction.atomic():
            if job.status == WordSaladRecheckJob.STATUS_SUPERSEDED or task.attempt_revision != job.task_revision:
                WordSaladRecheckItem.objects.filter(
                    pk=item.pk, status=WordSaladRecheckItem.STATUS_RUNNING, claim_token=item_token,
                ).update(
                    status=WordSaladRecheckItem.STATUS_SUPERSEDED,
                    claimed_until=None,
                    claim_token=None,
                    updated_at=timezone.now(),
                )
                WordSaladRecheckJob.objects.filter(
                    pk=job.pk, status=WordSaladRecheckJob.STATUS_RUNNING, claim_token=job.claim_token,
                ).update(claim_token=None, claimed_until=None, updated_at=timezone.now())
                return 'superseded'
            if actor is not None:
                result = recheck_word_salad_actor(task, game=job.game, notify=False, **actor)
                credited = int(result.get('credited') or 0)
            now = timezone.now()
            updated = WordSaladRecheckItem.objects.filter(
                pk=item.pk, status=WordSaladRecheckItem.STATUS_RUNNING, claim_token=item_token,
            ).update(status=WordSaladRecheckItem.STATUS_COMPLETED, credited_attempts=credited,
                     completed_at=now, claimed_until=None, claim_token=None, updated_at=now)
            if not updated:
                return 'lease_lost'
            WordSaladRecheckJob.objects.filter(
                pk=job.pk, status=WordSaladRecheckJob.STATUS_RUNNING, claim_token=job.claim_token,
            ).update(
                completed_actors=F('completed_actors') + 1,
                credited_attempts=F('credited_attempts') + credited,
                status=WordSaladRecheckJob.STATUS_PENDING,
                next_attempt_at=now,
                claimed_until=None,
                claim_token=None,
                updated_at=now,
            )
            if not WordSaladRecheckItem.objects.filter(job_id=job.pk).exclude(
                status__in=(WordSaladRecheckItem.STATUS_COMPLETED, WordSaladRecheckItem.STATUS_SUPERSEDED),
            ).exists():
                WordSaladRecheckJob.objects.filter(pk=job.pk, status=WordSaladRecheckJob.STATUS_PENDING).update(
                    status=WordSaladRecheckJob.STATUS_COMPLETED, completed_at=now,
                    claim_token=None, claimed_until=None, updated_at=now,
                )
            if actor is not None:
                _schedule_actor_notification(task, actor, job.game)
        logger.info(
            'word_salad_task_completed job_id=%s item_id=%s task_revision=%s duration_ms=%.1f credited=%s',
            job.pk, item.pk, job.task_revision, (time.perf_counter() - started) * 1000, credited,
        )
        return 'completed'
    except Exception as exc:
        logger.exception(
            'word_salad_task_failed job_id=%s item_id=%s task_revision=%s duration_ms=%.1f',
            job.pk, item.pk, job.task_revision, (time.perf_counter() - started) * 1000,
        )
        _record_item_failure(job, item, item_token, exc)
        return 'failed'


def process_word_salad_recheck_item(*, job_id, item_id, worker='worker'):
    """Process exactly one authoritative DB item; transport-agnostic."""
    state, job, item, item_token = _claim_specific_item(job_id=job_id, item_id=item_id, worker=worker)
    if state != 'claimed':
        logger.info(
            'word_salad_task_%s job_id=%s item_id=%s',
            'duplicate' if state == 'completed' else 'stale' if state == 'superseded' else 'lease_conflict' if state == 'lease_conflict' else 'skipped',
            job_id, item_id,
        )
        return state
    return _process_claimed_item(job, item, item_token)


def process_word_salad_rechecks(*, limit=1, worker='cron'):
    processed = 0
    for _ in range(max(0, int(limit))):
        job, item, _item_token = _claim_next(worker=worker)
        if job is None:
            break
        if item is None:
            processed += 1
            continue
        result = _process_claimed_item(job, item, _item_token)
        if result in ('completed', 'superseded', 'failed'):
            processed += 1
    return processed
