"""Claim and refresh due ``DailyGameDifficulty`` rows from the minute cron.

Row locks are held only while assigning a claim token. The heavy
``calculate_game_difficulty`` call runs outside that transaction. Correctness
across Elastic Beanstalk instances comes from the database, not ``flock``.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from uuid import UUID

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from games.difficulty import (
    DUE_REFRESH_LIMIT,
    REFRESH_CLAIM_LEASE,
    SUPPORTED_GAME_IDS,
    calculate_game_difficulty,
    ensure_recent_difficulty_rows,
    persist_difficulty_snapshot,
    refresh_stale_historical_norms,
    retry_delay_for_fail_count,
)
from games.models import DailyDifficultyQueueStatus, DailyGameDifficulty, GameTaskGroup

logger = logging.getLogger('application')


@dataclass(frozen=True)
class ClaimedDifficultyRefresh:
    snapshot_id: int
    placement: GameTaskGroup
    token: UUID
    claimed_revision: int


def due_daily_difficulty_queryset(*, now=None, game_ids=None):
    """Index-friendly due set: dirty rows whose throttle and lease have expired."""
    now = now or timezone.now()
    queryset = DailyGameDifficulty.objects.filter(
        dirty=True,
        refresh_not_before__lte=now,
    ).filter(
        Q(refresh_claimed_until__isnull=True) | Q(refresh_claimed_until__lte=now),
    )
    if game_ids:
        queryset = queryset.filter(placement__game_id__in=tuple(game_ids))
    return queryset.select_related(
        'placement',
        'placement__game',
        'placement__task_group',
    ).order_by(F('published_at').desc(nulls_last=True), 'id')


def _lock_queryset(queryset):
    if not connection.features.has_select_for_update:
        return queryset
    kwargs = {}
    # SKIP LOCKED lets another EB instance take a different due row instead of waiting.
    if connection.features.has_select_for_update_skip_locked:
        kwargs['skip_locked'] = True
    return queryset.select_for_update(**kwargs)


def claim_due_daily_difficulties(*, now=None, limit=DUE_REFRESH_LIMIT, game_ids=None):
    """Claim a batch of due rows, then release locks before calculation."""
    now = now or timezone.now()
    limit = max(0, int(limit))
    if limit == 0:
        return []
    lease_until = now + REFRESH_CLAIM_LEASE
    claimed = []
    with transaction.atomic():
        rows = list(_lock_queryset(due_daily_difficulty_queryset(now=now, game_ids=game_ids))[:limit])
        for row in rows:
            token = uuid.uuid4()
            # Atomic WHERE keeps SQLite (no SKIP LOCKED) from double-claiming a lease.
            updated = DailyGameDifficulty.objects.filter(pk=row.pk).filter(
                Q(refresh_claimed_until__isnull=True) | Q(refresh_claimed_until__lte=now),
            ).update(
                refresh_claim_token=token,
                refresh_claimed_until=lease_until,
            )
            if updated != 1:
                continue
            claimed.append(ClaimedDifficultyRefresh(
                snapshot_id=row.pk,
                placement=row.placement,
                token=token,
                claimed_revision=row.data_revision,
            ))
    return claimed


def record_difficulty_refresh_failure(claim, exc, *, now=None):
    now = now or timezone.now()
    message = '{}: {}'.format(exc.__class__.__name__, exc)
    snapshot = DailyGameDifficulty.objects.filter(pk=claim.snapshot_id).first()
    fail_count = int(snapshot.refresh_fail_count or 0) + 1 if snapshot else 1
    delay = retry_delay_for_fail_count(fail_count)
    updated = DailyGameDifficulty.objects.filter(
        pk=claim.snapshot_id,
        refresh_claim_token=claim.token,
    ).update(
        refresh_fail_count=F('refresh_fail_count') + 1,
        refresh_not_before=now + delay,
        refresh_last_error=message[:2000],
        refresh_claim_token=None,
        refresh_claimed_until=None,
    )
    return updated


def refresh_claimed_difficulty(claim, *, now=None):
    now = now or timezone.now()
    try:
        result = calculate_game_difficulty(claim.placement, now=now, save=False)
        if result is None:
            record_difficulty_refresh_failure(
                claim,
                RuntimeError('calculate_game_difficulty returned None'),
                now=now,
            )
            return None
        written = persist_difficulty_snapshot(
            claim.placement,
            result,
            now=now,
            claimed_revision=claim.claimed_revision,
            claim_token=claim.token,
        )
        if not written:
            logger.info(
                'Daily difficulty refresh skipped stale claim for %s/%s (id=%s)',
                claim.placement.game_id,
                claim.placement.number,
                claim.snapshot_id,
            )
            return None
        return result
    except Exception as exc:
        logger.exception(
            'Daily difficulty refresh failed for %s/%s (id=%s)',
            claim.placement.game_id,
            claim.placement.number,
            claim.snapshot_id,
        )
        record_difficulty_refresh_failure(claim, exc, now=now)
        return None


def refresh_due_daily_difficulties(
    *,
    now=None,
    limit=DUE_REFRESH_LIMIT,
    game_ids=None,
    dry_run=False,
):
    """One cron tick: ensure recent rows, refresh stale norms, claim, calculate."""
    now = now or timezone.now()
    game_ids = tuple(game_ids) if game_ids else None
    ensure_recent_difficulty_rows(now=now, game_ids=game_ids)
    refresh_stale_historical_norms(now=now, game_ids=game_ids)
    if dry_run:
        return [
            {
                'game_id': row.placement.game_id,
                'placement_id': row.placement_id,
                'number': row.placement.number,
                'data_revision': row.data_revision,
                'calculated_revision': row.calculated_revision,
                'refresh_not_before': row.refresh_not_before,
            }
            for row in due_daily_difficulty_queryset(now=now, game_ids=game_ids)[: max(0, int(limit))]
        ]
    claimed = claim_due_daily_difficulties(now=now, limit=limit, game_ids=game_ids)
    refreshed = []
    for claim in claimed:
        result = refresh_claimed_difficulty(claim, now=now)
        if result:
            refreshed.append(result)
    return refreshed


def run_daily_difficulty_refresh(
    *,
    now=None,
    limit=DUE_REFRESH_LIMIT,
    game_ids=None,
    worker='cron',
):
    """Run one tick and persist a singleton heartbeat for the admin dashboard."""
    started_at = now or timezone.now()
    worker_label = '{}:{}:{}'.format(worker, socket.gethostname(), os.getpid())[:255]
    DailyDifficultyQueueStatus.objects.update_or_create(
        pk=1,
        defaults={
            'last_started_at': started_at,
            'last_error': '',
            'last_limit': max(0, int(limit)),
            'last_worker': worker_label,
        },
    )
    try:
        results = refresh_due_daily_difficulties(
            now=now,
            limit=limit,
            game_ids=game_ids,
        )
    except Exception as exc:
        DailyDifficultyQueueStatus.objects.filter(pk=1).update(
            last_finished_at=timezone.now(),
            last_error='{}: {}'.format(exc.__class__.__name__, exc)[:2000],
        )
        raise
    finished_at = timezone.now()
    DailyDifficultyQueueStatus.objects.filter(pk=1).update(
        last_finished_at=finished_at,
        last_success_at=finished_at,
        last_error='',
        last_refreshed_count=len(results),
    )
    return results


def repair_daily_difficulty_queue(*, now=None):
    """Repair recoverable queue metadata without doing heavy calculations."""
    from games.difficulty import backfill_daily_difficulty_rows

    now = now or timezone.now()
    rescheduled = DailyGameDifficulty.objects.filter(
        calculated_at__isnull=True,
        dirty=True,
        published_at__lte=now,
    ).filter(
        Q(refresh_not_before__isnull=True) | Q(refresh_not_before__gt=now),
    ).count()
    created = backfill_daily_difficulty_rows(now=now)
    released = DailyGameDifficulty.objects.filter(
        refresh_claimed_until__lte=now,
    ).exclude(refresh_claim_token__isnull=True).update(
        refresh_claim_token=None,
        refresh_claimed_until=None,
    )
    # ensure/backfill above synchronizes first-run deadlines to the current
    # publication schedule. Count rows that became immediately eligible.
    due = due_daily_difficulty_queryset(now=now).count()
    return {
        'created': created,
        'rescheduled': rescheduled,
        'released': released,
        'due': due,
    }
