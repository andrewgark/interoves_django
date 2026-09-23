"""Canonical completion transaction for normal gameplay requests.

Phase 1 deliberately does not introduce a coordinator table.  The existing
unique ``PlayerCompletedGame`` keys and replay ``run_id`` remain the
idempotency boundary.  The canonical lock order in this module is:

    authoritative final task state -> DailySolveTiming -> PlayerCompletedGame

Analytics, projections, notifications and cache invalidation are performed by
callers after this function returns successfully.
"""

from __future__ import annotations

import logging
import time

from django.db import transaction
from django.utils import timezone

from games.analytics import (
    _analytics_actor_kwargs,
    _ensure_completed_record,
    game_instance_id_for_task_group,
    is_task_group_complete,
    supported_game_kind,
)
from games.daily_timing import complete_daily_timing_in_transaction
from games.models import ChainTaskState, PlayerCompletedGame
from games.replay import StaleReplayError


logger = logging.getLogger(__name__)


def _actor_values(actor):
    if actor is None:
        return None
    if isinstance(actor, dict):
        return {
            'team': actor.get('team'),
            'user': actor.get('user'),
            'anon_key': actor.get('anon_key'),
        }
    return {
        'team': getattr(actor, 'team', None),
        'user': getattr(actor, 'user', None),
        'anon_key': getattr(actor, 'anon_key', None),
    }


def _lock_authoritative_state(*, task, game, actor, task_group, replay_slot, mode):
    """Reread the final chain state under the completion lock when present.

    The answer transaction has already committed this row.  Locking it again
    here makes the completion tail deterministic without taking a broad
    GameTaskGroup lock.  Non-chain tasks have no actor state row to lock here;
    their authoritative Task lock is already taken by ``check_attempt``.
    """
    if task is None or task.task_type not in {
        'raddle', 'replacements_lines', 'alphabetty', 'word_salad',
    }:
        return None
    game_mode = 'tournament' if mode == 'tournament' else 'general'
    filters = {
        'team': actor['team'],
        'user': actor['user'],
        'anon_key': actor['anon_key'],
        'task': task,
        'game': game,
        'game_mode': game_mode,
        'replay_slot': replay_slot,
    }
    return ChainTaskState.objects.select_for_update().filter(**filters).first()


def complete_logical_game(
    *,
    actor,
    game,
    task_group,
    task=None,
    replay_slot=None,
    run_id=None,
    analytics_user=None,
    result=PlayerCompletedGame.RESULT_SOLVED,
    mode='general',
    source='task_completion',
    now=None,
):
    """Atomically finalize one already-persisted logical game completion.

    ``check_attempt`` remains responsible for persisting the answer.  This
    function owns the canonical completion tail after that commit: completion
    verification, timing freeze, official completion row, or replay status.
    """
    actor = _actor_values(actor)
    if actor is None or task_group is None or game is None:
        return None
    if sum(value is not None and value != '' for value in actor.values()) != 1:
        return None
    if replay_slot is not None:
        if run_id is None or str(replay_slot.run_id) != str(run_id):
            raise StaleReplayError()
    elif run_id is not None:
        raise StaleReplayError()

    game_kind = supported_game_kind(game)
    if not game_kind:
        return None
    now = now or timezone.now()
    timing_phases = {}
    started = time.perf_counter()
    transaction_started = None
    record = None
    created = False
    timing = None
    replay_completed = False
    try:
        with transaction.atomic():
            transaction_started = time.perf_counter()
            _lock_authoritative_state(
                task=task,
                game=game,
                actor=actor,
                task_group=task_group,
                replay_slot=replay_slot,
                mode=mode,
            )
            if not is_task_group_complete(
                task_group=task_group,
                game=game,
                team=actor['team'],
                user=actor['user'],
                anon_key=actor['anon_key'],
                mode=mode,
                replay_slot=replay_slot,
            ):
                return None

            if replay_slot is not None:
                updated = type(replay_slot).objects.filter(
                    pk=replay_slot.pk,
                    run_id=replay_slot.run_id,
                    status__in=('active', 'completed'),
                ).exclude(status='completed').update(
                    status='completed',
                    updated_at=now,
                )
                replay_completed = bool(updated) or type(replay_slot).objects.filter(
                    pk=replay_slot.pk,
                    run_id=replay_slot.run_id,
                    status='completed',
                ).exists()
            else:
                timing = complete_daily_timing_in_transaction(
                    game=game,
                    task_group=task_group,
                    team=actor['team'],
                    user=actor['user'],
                    anon_key=actor['anon_key'],
                    replay_slot=None,
                    now=now,
                    timing_phases=timing_phases,
                )
                analytics_actor = _analytics_actor_kwargs(
                    analytics_user=analytics_user,
                    user=actor['user'],
                    anon_key=actor['anon_key'],
                )
                if analytics_actor is None or task is None:
                    return None
                instance_id = game_instance_id_for_task_group(game, task_group)
                lookup = dict(analytics_actor, game_instance_id=instance_id)
                # Existing rows are locked before reread/update.  First
                # insert races remain protected by the existing unique index
                # and create_or_reread_analytics_row fallback.
                PlayerCompletedGame.objects.select_for_update().filter(
                    **lookup,
                ).first()
                record, created = _ensure_completed_record(
                    **analytics_actor,
                    game=game,
                    task=task,
                    game_kind=game_kind,
                    result=result,
                    is_backfilled=False,
                    source=source,
                    mode=mode,
                    completion_team=actor['team'],
                    completion_user=actor['user'],
                    completion_anon_key=actor['anon_key'],
                    _group_is_complete=True,
                )
                if record is None:
                    return None
    finally:
        total_ms = (time.perf_counter() - started) * 1000.0
        tx_ms = (
            (time.perf_counter() - transaction_started) * 1000.0
            if transaction_started is not None else 0.0
        )
        logger.info(
            'completion_coordinator_timing total_ms=%.1f transaction_ms=%.1f '
            'timing_lock_ms=%.1f duplicate=%s replay=%s source=%s',
            total_ms,
            tx_ms,
            timing_phases.get('timing_lock_ms', 0.0),
            bool(record is not None and not created),
            replay_slot is not None,
            source,
        )

    return {
        'record': record,
        'created': created,
        'timing': timing,
        'replay_completed': replay_completed,
        'replay': replay_slot is not None,
    }
