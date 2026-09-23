"""Durable anonymous-profile merge queue.

The queue deliberately uses MySQL as its coordination layer.  EB runs the
minute command on every instance; row leases and fencing tokens make that
safe without turning Redis into a task broker.
"""

from __future__ import annotations

import logging
import socket
import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from games.anon_migrate import (
    ANON_MIGRATION_STEPS,
    anon_migration_counts,
    migrate_anon_history_step,
)
from games.models import (
    AnonAccountClaim,
    AnonymousMergeJob,
    AnonymousMergeReconcileItem,
    HiddenAnonKey,
    StatisticsEvent,
)
from games.targeted_completion_reconciliation import completion_pairs_for_actor

logger = logging.getLogger('application')

MERGE_LEASE = timedelta(minutes=2)
RECONCILE_ITEM_LEASE = timedelta(minutes=2)
MAX_JOB_ATTEMPTS = 8
MAX_ITEM_ATTEMPTS = 5
JOB_BATCH_SIZE = 5
MOVE_STEPS = tuple(step for step in ANON_MIGRATION_STEPS if step not in ('prepare', 'reconcile', 'finish'))


def _retry_delay(attempt_count):
    return timedelta(seconds=min(900, 15 * (2 ** max(0, int(attempt_count) - 1))))


def _due_query(queryset, now):
    return queryset.filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))


def _lock_queryset(queryset):
    if not connection.features.has_select_for_update:
        return queryset
    kwargs = {}
    if connection.features.has_select_for_update_skip_locked:
        kwargs['skip_locked'] = True
    return queryset.select_for_update(**kwargs)


def _job_stage_progress(job):
    if job.status == AnonymousMergeJob.STATUS_COMPLETED:
        return 100
    if job.stage == AnonymousMergeJob.STAGE_ANALYZING:
        return 0
    if job.stage == AnonymousMergeJob.STAGE_MOVING:
        total = max(1, len(MOVE_STEPS))
        done = MOVE_STEPS.index(job.move_step) if job.move_step in MOVE_STEPS else 0
        return min(35, int(5 + 30 * done / total))
    if job.stage == AnonymousMergeJob.STAGE_RECONCILING:
        total = max(1, job.total_reconciliation_units)
        return min(95, 35 + int(60 * job.completed_reconciliation_units / total))
    return 98


def serialize_merge_job(job):
    return {
        'id': str(job.id),
        'status': job.status,
        'stage': job.stage,
        'total_submissions': job.total_submissions,
        'moved_submissions': job.moved_submissions,
        **(job.moved_counts or {}),
        'total_reconciliation_units': job.total_reconciliation_units,
        'completed_reconciliation_units': job.completed_reconciliation_units,
        'attempt_count': job.attempt_count,
        'progress': _job_stage_progress(job),
        'error': 'merge_failed' if job.status == AnonymousMergeJob.STATUS_FAILED else '',
    }


@transaction.atomic
def enqueue_anonymous_merge(user, anon_key):
    """Claim the source identity and create/reuse a durable merge job."""
    if HiddenAnonKey.objects.select_for_update().filter(anon_key=anon_key).exists():
        return None, 'hidden_anon'

    claim = AnonAccountClaim.objects.select_for_update().filter(anon_key=anon_key).first()
    if claim is not None and claim.user_id != user.pk:
        return None, 'claimed_elsewhere'
    if claim is None:
        counts = anon_migration_counts(anon_key)
        if not any(counts.values()):
            return None, 'empty'
        try:
            with transaction.atomic():
                claim = AnonAccountClaim.objects.create(anon_key=anon_key, user=user)
        except IntegrityError:
            claim = AnonAccountClaim.objects.select_for_update().get(anon_key=anon_key)
            if claim.user_id != user.pk:
                return None, 'claimed_elsewhere'

    job = AnonymousMergeJob.objects.select_for_update().filter(anon_key=anon_key).first()
    if job is None:
        try:
            with transaction.atomic():
                job = AnonymousMergeJob.objects.create(
                    anon_key=anon_key,
                    user=user,
                    move_step=MOVE_STEPS[0],
                )
        except IntegrityError:
            job = AnonymousMergeJob.objects.select_for_update().get(anon_key=anon_key)
    elif job.user_id != user.pk:
        return None, 'claimed_elsewhere'
    elif job.status == AnonymousMergeJob.STATUS_FAILED:
        job.status = AnonymousMergeJob.STATUS_PENDING
        job.next_attempt_at = timezone.now()
        job.last_error = ''
        job.save(update_fields=['status', 'next_attempt_at', 'last_error', 'updated_at'])
        _reset_unfinished_reconcile_items(job)
    return job, 'ok'


def active_merge_job_for_user(user):
    return AnonymousMergeJob.objects.filter(
        user=user,
        status__in=(
            AnonymousMergeJob.STATUS_PENDING,
            AnonymousMergeJob.STATUS_RUNNING,
            AnonymousMergeJob.STATUS_FAILED,
        ),
    ).order_by('-created_at').first()


@transaction.atomic
def retry_merge_job(user, job_id):
    job = AnonymousMergeJob.objects.select_for_update().filter(pk=job_id, user=user).first()
    if job is None or job.status != AnonymousMergeJob.STATUS_FAILED:
        return None
    job.status = AnonymousMergeJob.STATUS_PENDING
    job.last_error = ''
    job.next_attempt_at = timezone.now()
    job.attempt_count = 0
    job.claim_token = None
    job.claimed_until = None
    job.save(update_fields=[
        'status', 'last_error', 'next_attempt_at', 'attempt_count',
        'claim_token', 'claimed_until', 'updated_at',
    ])
    _reset_unfinished_reconcile_items(job)
    return job


def _reset_unfinished_reconcile_items(job):
    """Make a failed job's unfinished units eligible for a fresh retry."""
    job.reconcile_items.exclude(
        status=AnonymousMergeReconcileItem.STATUS_COMPLETED,
    ).update(
        status=AnonymousMergeReconcileItem.STATUS_PENDING,
        attempt_count=0,
        last_error='',
        claim_token=None,
        claimed_until=None,
        next_attempt_at=None,
        completed_at=None,
        updated_at=timezone.now(),
    )


def claim_next_merge_job(*, now=None, worker='cron'):
    now = now or timezone.now()
    due = _due_query(
        AnonymousMergeJob.objects.filter(
            Q(status=AnonymousMergeJob.STATUS_PENDING)
            | Q(status=AnonymousMergeJob.STATUS_RUNNING, claimed_until__lte=now),
        ),
        now,
    ).order_by('created_at')
    with transaction.atomic():
        job = _lock_queryset(due).first()
        if job is None:
            return None
        token = uuid.uuid4()
        job.status = AnonymousMergeJob.STATUS_RUNNING
        job.claim_token = token
        job.claimed_until = now + MERGE_LEASE
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.next_attempt_at = None
        job.save(update_fields=[
            'status', 'claim_token', 'claimed_until', 'attempt_count',
            'started_at', 'next_attempt_at', 'updated_at',
        ])
    logger.info(
        'anonymous merge claimed job=%s worker=%s host=%s stage=%s attempt=%s',
        job.id, worker, socket.gethostname(), job.stage, job.attempt_count,
    )
    return job, token


def _renew_job(job, token):
    return AnonymousMergeJob.objects.filter(
        pk=job.pk, status=AnonymousMergeJob.STATUS_RUNNING, claim_token=token,
    ).update(claimed_until=timezone.now() + MERGE_LEASE)


def _record_job_failure(job, token, exc):
    message = '{}: {}'.format(exc.__class__.__name__, exc)[:2000]
    exhausted = job.attempt_count >= MAX_JOB_ATTEMPTS
    updates = {
        'status': AnonymousMergeJob.STATUS_FAILED if exhausted else AnonymousMergeJob.STATUS_PENDING,
        'last_error': message,
        'next_attempt_at': None if exhausted else timezone.now() + _retry_delay(job.attempt_count),
        'claim_token': None,
        'claimed_until': None,
    }
    AnonymousMergeJob.objects.filter(pk=job.pk, claim_token=token).update(**updates)
    logger.exception('anonymous merge failed job=%s exhausted=%s', job.id, exhausted)


def _analyze(job, token):
    now = timezone.now()
    with transaction.atomic():
        locked = AnonymousMergeJob.objects.select_for_update().get(pk=job.pk)
        if locked.claim_token != token or locked.status != AnonymousMergeJob.STATUS_RUNNING:
            return False
        counts = anon_migration_counts(locked.anon_key)
        pairs = sorted(completion_pairs_for_actor(anon_key=locked.anon_key))
        AnonymousMergeReconcileItem.objects.bulk_create([
            AnonymousMergeReconcileItem(job=locked, game_id=game_id, task_group_id=task_group_id)
            for game_id, task_group_id in pairs
        ], ignore_conflicts=True)
        total_pairs = AnonymousMergeReconcileItem.objects.filter(job=locked).count()
        locked.total_submissions = counts.get('attempts', 0)
        locked.total_reconciliation_units = total_pairs
        locked.stage = AnonymousMergeJob.STAGE_MOVING
        locked.move_step = MOVE_STEPS[0]
        locked.claimed_until = now + MERGE_LEASE
        locked.save(update_fields=[
            'total_submissions', 'total_reconciliation_units', 'stage', 'move_step',
            'claimed_until', 'updated_at',
        ])
    return True


def _move_one_step(job, token):
    if not _renew_job(job, token):
        return False
    result = migrate_anon_history_step(job.user, job.anon_key, job.move_step)
    next_index = MOVE_STEPS.index(job.move_step) + 1
    with transaction.atomic():
        locked = AnonymousMergeJob.objects.select_for_update().get(pk=job.pk)
        if locked.claim_token != token or locked.status != AnonymousMergeJob.STATUS_RUNNING:
            return False
        if result.get('moved'):
            locked.moved_submissions = max(locked.moved_submissions, result['moved'])
        moved_counts = dict(locked.moved_counts or {})
        for key, value in result.items():
            if key.startswith('moved_') or key == 'moved':
                moved_counts[key] = moved_counts.get(key, 0) + int(value or 0)
        locked.moved_counts = moved_counts
        if next_index >= len(MOVE_STEPS):
            locked.stage = AnonymousMergeJob.STAGE_RECONCILING
            locked.move_step = ''
        else:
            locked.move_step = MOVE_STEPS[next_index]
        locked.claimed_until = timezone.now() + MERGE_LEASE
        locked.save(update_fields=['moved_submissions', 'moved_counts', 'stage', 'move_step', 'claimed_until', 'updated_at'])
    return True


def _claim_reconcile_item(job, token):
    now = timezone.now()
    due = _due_query(
        AnonymousMergeReconcileItem.objects.filter(job=job).filter(
            Q(status=AnonymousMergeReconcileItem.STATUS_PENDING)
            | Q(status=AnonymousMergeReconcileItem.STATUS_RUNNING, claimed_until__lte=now)
            | Q(status=AnonymousMergeReconcileItem.STATUS_FAILED, attempt_count__lt=MAX_ITEM_ATTEMPTS),
        ),
        now,
    ).order_by('id')
    with transaction.atomic():
        item = _lock_queryset(due).first()
        if item is None:
            return None
        token = uuid.uuid4()
        item.status = AnonymousMergeReconcileItem.STATUS_RUNNING
        item.claim_token = token
        item.claimed_until = now + RECONCILE_ITEM_LEASE
        item.attempt_count += 1
        item.next_attempt_at = None
        item.save(update_fields=['status', 'claim_token', 'claimed_until', 'attempt_count', 'next_attempt_at', 'updated_at'])
    return item, token


def _reconcile_one(job, job_token):
    claimed = _claim_reconcile_item(job, job_token)
    if claimed is None:
        return False
    item, item_token = claimed
    try:
        from games.daily_result_projection import mark_projection_dirty
        from games.models import Game, TaskGroup
        from games.targeted_completion_reconciliation import reconcile_task_group_actors

        with transaction.atomic():
            current_job = AnonymousMergeJob.objects.select_for_update().get(pk=job.pk)
            if current_job.claim_token != job_token or current_job.status != AnonymousMergeJob.STATUS_RUNNING:
                return False
            game = Game.objects.filter(pk=item.game_id, project_id='sections').first()
            task_group = TaskGroup.objects.filter(pk=item.task_group_id).first()
            if game is not None and task_group is not None:
                mark_projection_dirty(game, task_group, full=True)
            reconcile_task_group_actors(
                game_id=item.game_id,
                task_group_id=item.task_group_id,
                actor_keys={(None, job.user_id, None)},
            )
            updated = AnonymousMergeReconcileItem.objects.filter(
                pk=item.pk, status=AnonymousMergeReconcileItem.STATUS_RUNNING, claim_token=item_token,
            ).update(
                status=AnonymousMergeReconcileItem.STATUS_COMPLETED,
                completed_at=timezone.now(),
                claimed_until=None,
                claim_token=None,
                last_error='',
            )
            if updated:
                current_job.completed_reconciliation_units = AnonymousMergeReconcileItem.objects.filter(
                    job=current_job, status=AnonymousMergeReconcileItem.STATUS_COMPLETED,
                ).count()
                current_job.claimed_until = timezone.now() + MERGE_LEASE
                current_job.save(update_fields=['completed_reconciliation_units', 'claimed_until', 'updated_at'])
        return True
    except Exception as exc:
        message = '{}: {}'.format(exc.__class__.__name__, exc)[:2000]
        exhausted = item.attempt_count >= MAX_ITEM_ATTEMPTS
        AnonymousMergeReconcileItem.objects.filter(pk=item.pk, claim_token=item_token).update(
            status=AnonymousMergeReconcileItem.STATUS_FAILED if exhausted else AnonymousMergeReconcileItem.STATUS_PENDING,
            last_error=message,
            next_attempt_at=None if exhausted else timezone.now() + _retry_delay(item.attempt_count),
            claimed_until=None,
            claim_token=None,
        )
        logger.exception('anonymous merge reconcile failed job=%s item=%s exhausted=%s', job.id, item.id, exhausted)
        return True


def _finalize(job, token):
    with transaction.atomic():
        locked = AnonymousMergeJob.objects.select_for_update().get(pk=job.pk)
        if locked.claim_token != token or locked.status != AnonymousMergeJob.STATUS_RUNNING:
            return False
        remaining = anon_migration_counts(locked.anon_key)
        if any(remaining.values()):
            raise RuntimeError('anonymous source still has rows: {}'.format(remaining))
        moved_any = any(int(value or 0) for value in (locked.moved_counts or {}).values())
        if not locked.event_recorded and (moved_any or locked.total_submissions):
            event_counts = dict(locked.moved_counts or {})
            event_counts.pop('moved', None)
            StatisticsEvent.record(
                StatisticsEvent.KIND_ANON_ATTEMPTS_MIGRATED,
                user=locked.user,
                anon_key=locked.anon_key,
                moved=locked.moved_submissions,
                reconciliation_units=locked.total_reconciliation_units,
                **event_counts,
            )
        locked.status = AnonymousMergeJob.STATUS_COMPLETED
        locked.stage = AnonymousMergeJob.STAGE_FINALIZING
        locked.event_recorded = True
        locked.claim_token = None
        locked.claimed_until = None
        locked.completed_at = timezone.now()
        locked.completed_reconciliation_units = locked.total_reconciliation_units
        locked.save(update_fields=[
            'status', 'stage', 'event_recorded', 'claim_token', 'claimed_until',
            'completed_at', 'completed_reconciliation_units', 'updated_at',
        ])
    logger.info(
        'auth_account_claim_completed job=%s user_id=%s moved_submissions=%s reconciliation=%s',
        job.id, job.user_id, job.moved_submissions, job.total_reconciliation_units,
    )
    return True


def process_merge_job(job, token, *, max_operations=JOB_BATCH_SIZE):
    """Advance one claimed job by bounded, independently committed units."""
    try:
        operations = 0
        while operations < max_operations:
            job.refresh_from_db()
            if job.claim_token != token or job.status != AnonymousMergeJob.STATUS_RUNNING:
                return
            if job.stage == AnonymousMergeJob.STAGE_ANALYZING:
                _analyze(job, token)
            elif job.stage == AnonymousMergeJob.STAGE_MOVING:
                _move_one_step(job, token)
            elif job.stage == AnonymousMergeJob.STAGE_RECONCILING:
                if _reconcile_one(job, token):
                    operations += 1
                    continue
                exhausted = job.reconcile_items.filter(
                    status=AnonymousMergeReconcileItem.STATUS_FAILED,
                    attempt_count__gte=MAX_ITEM_ATTEMPTS,
                ).exists()
                retryable = job.reconcile_items.filter(
                    Q(status=AnonymousMergeReconcileItem.STATUS_PENDING)
                    | Q(status=AnonymousMergeReconcileItem.STATUS_RUNNING, claimed_until__lte=timezone.now())
                    | Q(status=AnonymousMergeReconcileItem.STATUS_FAILED, attempt_count__lt=MAX_ITEM_ATTEMPTS),
                ).exists()
                if exhausted:
                    raise RuntimeError('reconciliation retry limit exhausted')
                if retryable:
                    return
                with transaction.atomic():
                    locked = AnonymousMergeJob.objects.select_for_update().get(pk=job.pk)
                    locked.stage = AnonymousMergeJob.STAGE_FINALIZING
                    locked.save(update_fields=['stage', 'updated_at'])
            elif job.stage == AnonymousMergeJob.STAGE_FINALIZING:
                _finalize(job, token)
                return
            operations += 1
    except Exception as exc:
        _record_job_failure(job, token, exc)


def run_anonymous_merge_queue(*, limit=1, worker='cron'):
    processed = 0
    for _ in range(max(0, int(limit))):
        claimed = claim_next_merge_job(worker=worker)
        if claimed is None:
            break
        job, token = claimed
        process_merge_job(job, token)
        processed += 1
    return processed
