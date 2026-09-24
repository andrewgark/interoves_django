"""Durable actor-by-actor queue for Word Salad rechecks."""

from __future__ import annotations

import json
import logging
import socket
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from games.models import WordSaladRecheckItem, WordSaladRecheckJob
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
    WordSaladRecheckItem.objects.bulk_create([
        WordSaladRecheckItem(
            job=job, actor_key=_actor_key(actor), team_id=actor[0], user_id=actor[1],
            anon_key=actor[2], replay_slot_id=actor[3], next_attempt_at=now,
        ) for actor in actors
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


def process_word_salad_rechecks(*, limit=1, worker='cron'):
    processed = 0
    for _ in range(max(0, int(limit))):
        job, item, item_token = _claim_next(worker=worker)
        if job is None:
            break
        if item is None:
            processed += 1
            continue
        try:
            task = job.task
            if task.attempt_revision != job.task_revision:
                WordSaladRecheckJob.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
                    status=WordSaladRecheckJob.STATUS_SUPERSEDED, claim_token=None, claimed_until=None, updated_at=timezone.now(),
                )
                continue
            actor = _resolve_word_salad_actor(item.team_id, item.user_id, item.anon_key, item.replay_slot_id)
            credited = 0
            if actor is not None:
                result = recheck_word_salad_actor(task, game=job.game, notify=True, **actor)
                credited = int(result.get('credited') or 0)
            with transaction.atomic():
                updated = WordSaladRecheckItem.objects.filter(
                    pk=item.pk, status=WordSaladRecheckItem.STATUS_RUNNING, claim_token=item_token,
                ).update(status=WordSaladRecheckItem.STATUS_COMPLETED, credited_attempts=credited,
                         completed_at=timezone.now(), claimed_until=None, claim_token=None, updated_at=timezone.now())
                if not updated:
                    continue
                WordSaladRecheckJob.objects.filter(pk=job.pk, status=WordSaladRecheckJob.STATUS_RUNNING).update(
                    completed_actors=F('completed_actors') + 1,
                    credited_attempts=F('credited_attempts') + credited,
                    claimed_until=timezone.now() + JOB_LEASE, updated_at=timezone.now(),
                )
                if not WordSaladRecheckItem.objects.filter(job_id=job.pk).exclude(status=WordSaladRecheckItem.STATUS_COMPLETED).exists():
                    WordSaladRecheckJob.objects.filter(pk=job.pk, status=WordSaladRecheckJob.STATUS_RUNNING).update(
                        status=WordSaladRecheckJob.STATUS_COMPLETED, completed_at=timezone.now(),
                        claim_token=None, claimed_until=None, updated_at=timezone.now(),
                    )
            processed += 1
        except Exception as exc:
            logger.exception('word salad recheck failed job=%s item=%s', job.pk, item.pk)
            _record_item_failure(job, item, item_token, exc)
    return processed
