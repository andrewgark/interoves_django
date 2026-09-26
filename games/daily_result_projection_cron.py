"""Projection reconciliation shared by the Web cron and the background worker."""

import time
from contextlib import contextmanager

from games.cron_lock import distributed_cron_lock
from games.daily_result_projection import (
    _canonical_group_results,
    projection_state_is_valid,
    refresh_daily_result_projection,
)
from games.models import DailyResultProjectionState, GameTaskGroup


PROJECTION_CRON_LOCK_NAME = 'daily_result_projection_reconcile'
PROJECTION_CRON_LOCK_TTL_SECONDS = 120
PROJECTION_REPAIR_LIMIT = 5


@contextmanager
def daily_result_projection_cron_lock():
    with distributed_cron_lock(
        PROJECTION_CRON_LOCK_NAME,
        ttl_seconds=PROJECTION_CRON_LOCK_TTL_SECONDS,
    ) as acquired:
        yield acquired


def reconcile_projection_releases(
    *,
    apply=False,
    limit=10,
    game=None,
    releases=None,
    task_group=None,
    deep=False,
):
    """Scan projection state. ``apply=False`` does not rebuild.

    ``limit=None`` scans the whole catalog. A numeric limit matches the
    management command: dry-run stops after that many rows, apply repairs
    at most that many invalid rows.
    """
    started = time.perf_counter()
    qs = GameTaskGroup.objects.filter(
        game__project_id='sections',
    ).select_related('game', 'task_group').order_by('game_id', 'pk')
    if game:
        qs = qs.filter(game_id=game)
    if task_group:
        qs = qs.filter(task_group_id=task_group)
    if releases:
        qs = qs.filter(number__in=[str(value) for value in releases])

    repair_limit = None if limit is None else max(1, int(limit))
    scanned = valid = missing = stale = rebuilt = failed = 0
    repairs_considered = 0
    lines = []
    invalid = []
    with daily_result_projection_cron_lock() as acquired:
        if not acquired:
            return {
                'skipped_locked': True,
                'lines': ['projection reconciliation skipped: lock held'],
            }
        chunk = 100 if repair_limit is None else min(repair_limit, 100)
        for link in qs.iterator(chunk_size=chunk):
            if not apply and repair_limit is not None and scanned >= repair_limit:
                break
            scanned += 1
            state = DailyResultProjectionState.objects.filter(
                game=link.game, task_group=link.task_group,
            ).first()
            state_valid = projection_state_is_valid(state, link.game)
            needs_repair = not state_valid
            if state is None:
                missing += 1
                reason = 'missing_state'
            elif not state_valid:
                stale += 1
                reason = 'invalid_state'
            else:
                valid += 1
                reason = 'valid'

            canonical_count = None
            if deep or (needs_repair and not apply):
                canonical_count = len(_canonical_group_results(link.game, link.task_group))

            repaired = False
            if apply and (needs_repair or deep):
                if repair_limit is not None and repairs_considered >= repair_limit:
                    break
                repairs_considered += 1
                refresh_daily_result_projection(link.game, link.task_group)
                state = DailyResultProjectionState.objects.filter(
                    game=link.game, task_group=link.task_group,
                ).first()
                repaired = needs_repair
                if projection_state_is_valid(state, link.game):
                    rebuilt += 1
                else:
                    failed += 1

            if needs_repair:
                invalid.append({
                    'game_id': link.game_id,
                    'release': link.number,
                    'task_group_id': link.task_group_id,
                    'status': reason,
                })
            details = ' canonical_actors={}'.format(canonical_count) if canonical_count is not None else ''
            lines.append(
                '{} release={} task_group={} status={}{}{}'.format(
                    link.game_id, link.number, link.task_group_id, reason,
                    ' repaired' if repaired else '',
                    details,
                )
            )

    summary = (
        'mode={} scanned={} valid={} missing={} stale={} rebuilt={} failed={} elapsed_s={:.3f}'.format(
            'apply' if apply else 'dry-run',
            scanned, valid, missing, stale, rebuilt, failed,
            time.perf_counter() - started,
        )
    )
    return {
        'skipped_locked': False,
        'mode': 'apply' if apply else 'dry-run',
        'scanned': scanned,
        'valid': valid,
        'missing': missing,
        'stale': stale,
        'rebuilt': rebuilt,
        'failed': failed,
        'invalid': invalid,
        'lines': lines,
        'summary': summary,
    }
