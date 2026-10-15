"""Canonical section score materialization.

Gameplay scoring remains in ``AttemptsInfo`` and the existing task calculators.
This module only turns their output into a rebuildable actor/release projection.
"""
from collections import defaultdict
from decimal import Decimal
import random
from time import monotonic

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Min
from django.utils import timezone

from games.leaderboard import actor_key
from games.daily.registry import get_daily_game
from games.results.share import elapsed_seconds_between
from games.models import (
    Attempt, DailyResultProjection, DailyResultProjectionDirtyActor, DailySolveTiming,
    DailyTaskResultProjection,
    DailyResultProjectionState, GameTaskGroup, Task, Team,
)

logger = logging.getLogger('application')

# A small production sample keeps the projection read path self-checking
# without putting the canonical attempts query back on every page request.
TASK_PROJECTION_COMPARE_SAMPLE_RATE = getattr(
    settings, 'DAILY_TASK_RESULT_PROJECTION_COMPARE_SAMPLE_RATE', 0.01,
)

# Roll out task-cell reads by game.  The writer can prepare compatible games
# ahead of time. All registered ``attempts_info`` games use this path by
# default; Word Salad and tournament modes remain outside this read model.
TASK_PROJECTION_READ_GAME_IDS = getattr(
    settings, 'DAILY_TASK_RESULT_PROJECTION_READ_GAME_IDS',
    frozenset(('ladder', 'alphabetty', 'censorly')),
)

# Bump when canonical score adaptation semantics change; old releases then
# transparently return to the canonical full-compute path until rebuilt.
# Increment deliberately when score semantics in an adapter change. These are
# semantic contracts, not source hashes: a change in AttemptsInfo scoring,
# hint penalties, or Salad state scoring requires an explicit version bump.
SCORER_ADAPTER_VERSIONS = {
    # Version 2 adds the task-cell projection contract.  Existing release
    # rows must be rebuilt before either projection is authoritative.
    'attempts_info': 2,
    'salad_state': 1,
}


def scorer_adapter_version(game, task_group=None):
    definition = get_daily_game(getattr(game, 'id', None))
    adapter = (
        definition.projection_adapter_key
        if definition is not None and definition.projection_adapter_key
        else 'attempts_info'
    )
    return SCORER_ADAPTER_VERSIONS[adapter]


def _projection_actor(actor):
    key = actor_key(actor)
    if not key:
        return None
    kind, value = key
    if kind == 'team':
        return {'actor_type': kind, 'actor_key': str(value), 'team': actor, 'user': None, 'anon_key': None}
    if kind == 'user':
        return {'actor_type': kind, 'actor_key': str(value), 'team': None, 'user_id': value, 'anon_key': None}
    return {'actor_type': kind, 'actor_key': str(value), 'team': None, 'user': None, 'anon_key': str(value)}


def projection_state_is_valid(state, game):
    """Return whether a state row authorizes the projection read path."""
    return bool(
        state
        and state.coverage_complete
        and state.is_valid
        and not state.full_refresh_required
        and not state.pending_actor_refreshes
        and state.adapter_version == scorer_adapter_version(game, state.task_group_id)
    )


def task_projection_state_is_valid(state, game):
    """Return whether task-level cells may replace the attempts read path."""
    definition = get_daily_game(getattr(game, 'id', None))
    return bool(
        projection_state_is_valid(state, game)
        and (
            definition is None
            or definition.game_id in TASK_PROJECTION_READ_GAME_IDS
        )
        and (definition is None or definition.projection_adapter_key == 'attempts_info')
    )


def _state_for_update(game, task_group):
    """Get/create and lock a state row; caller must be in a transaction."""
    lookup = {'game': game, 'task_group': task_group}
    state = DailyResultProjectionState.objects.select_for_update().filter(**lookup).first()
    if state is not None:
        return state
    # Two first mutations for a newly materialized release can race. Keep the
    # unique-key retry in a savepoint so the outer canonical transaction stays
    # usable after MySQL/SQLite reports the competing insert.
    try:
        with transaction.atomic():
            DailyResultProjectionState.objects.get_or_create(
                defaults={
                    'adapter_version': scorer_adapter_version(game, task_group),
                    'coverage_complete': False,
                    'is_valid': False,
                    'full_refresh_required': True,
                },
                **lookup,
            )
    except IntegrityError:
        pass
    return DailyResultProjectionState.objects.select_for_update().get(**lookup)


def _actor_identity_from_filter(actor_filter):
    if actor_filter.get('team_id') is not None:
        return 'team', str(actor_filter['team_id'])
    if actor_filter.get('user_id') is not None:
        return 'user', str(actor_filter['user_id'])
    if actor_filter.get('anon_key'):
        return 'anon', str(actor_filter['anon_key'])
    return None


def mark_projection_dirty(game, task_group, *, actor=False, full=False, actor_filter=None):
    """Invalidate a release inside the caller's canonical mutation transaction.

    Actor refreshes are allowed to restore validity only when a prior full
    refresh established complete coverage.  Structural/content mutations
    require another full refresh.
    """
    if game is None or task_group is None or getattr(game, 'project_id', None) != 'sections':
        return None
    if not GameTaskGroup.objects.filter(game=game, task_group=task_group).exists():
        return None
    with transaction.atomic():
        if not actor and not DailyResultProjectionState.objects.filter(
            game=game, task_group=task_group,
        ).exists():
            # Missing coverage is already represented by the absence of the
            # row. Structural/content edits must not create a duplicate
            # marker merely to mark an unmaterialized release dirty.
            return None
        state = _state_for_update(game, task_group)
        state.source_revision = F('source_revision') + 1
        state.is_valid = False
        if full:
            state.full_refresh_required = True
        state.save(update_fields=[
            'source_revision', 'is_valid', 'full_refresh_required',
            'pending_actor_refreshes', 'completed_at',
        ])
        state.refresh_from_db(fields=['source_revision', 'pending_actor_refreshes'])
        if actor and actor_filter:
            identity = _actor_identity_from_filter(actor_filter)
            if identity:
                DailyResultProjectionDirtyActor.objects.update_or_create(
                    game=game, task_group=task_group,
                    actor_type=identity[0], actor_key=identity[1],
                    defaults={'source_revision': state.source_revision},
                )
                state.pending_actor_refreshes = DailyResultProjectionDirtyActor.objects.filter(
                    game=game, task_group=task_group,
                ).count()
                state.save(update_fields=['pending_actor_refreshes', 'completed_at'])
        return state.source_revision


def _canonical_group_results(game, task_group, *, actor_filter=None, with_task_rows=False):
    """Return exact existing AttemptsInfo scores for the release, grouped by actor."""
    from games.results.snapshot import results_attempts_scope_game

    tasks = list(Task.objects.visible().filter(task_group=task_group).exclude(task_type='text_with_forms').order_by('number', 'pk'))
    if not tasks:
        return ({}, {}) if with_task_rows else {}
    scope_game = results_attempts_scope_game(game, 'general')
    task_ids = [task.pk for task in tasks]
    from games.results_sql_aggregate import get_sql_aggregated_game_actor_rows, tasks_need_orm_results_aggregate
    if tasks_need_orm_results_aggregate(tasks):
        result_rows = Attempt.manager.get_bulk_game_actor_rows(
            task_ids, mode='general', game=scope_game, actor_filter=actor_filter, include_hidden=True,
        )
    else:
        result_rows = get_sql_aggregated_game_actor_rows(
            task_ids, game=scope_game, actor_filter=actor_filter, include_hidden=True,
        )
    attempt_times = {}
    if not tasks_need_orm_results_aggregate(tasks):
        attempt_qs = Attempt.manager.filter(task_id__in=task_ids, skip=False, replay_slot__isnull=True)
        if scope_game is not None:
            attempt_qs = attempt_qs.filter(game=scope_game)
        if actor_filter:
            attempt_qs = attempt_qs.filter(**actor_filter)
        for source in attempt_qs.values('team_id', 'user_id', 'anon_key').annotate(
            first_at=Min('time'), last_at=Max('time'),
        ):
            key = ('team', source['team_id']) if source['team_id'] is not None else (
                ('user', source['user_id']) if source['user_id'] is not None else ('anon', source['anon_key'])
            )
            if key[1] is not None:
                attempt_times[key] = (source['first_at'], source['last_at'])
    totals = defaultdict(lambda: {
        'actor': None, 'score': Decimal('0'), 'attempts_count': 0,
        'first_at': None, 'last_at': None, 'present': False,
    })
    for task in tasks:
        for actor, info in result_rows.get(task.pk, ()):
            if not (info.attempts or info.hint_attempts):
                continue
            key = actor_key(actor)
            if key is None:
                continue
            row = totals[key]
            row['actor'] = actor
            row['present'] = True
            row['score'] += Decimal(str(info.get_result_points() or 0))
            row['attempts_count'] += int(info.get_n_attempts() or 0)
            stamps = [a.time for a in (info.attempts or ()) if getattr(a, 'time', None)]
            if key in attempt_times:
                first, last = attempt_times[key]
                row['first_at'] = min(row['first_at'], first) if row['first_at'] else first
                row['last_at'] = max(row['last_at'], last) if row['last_at'] else last
            elif stamps:
                first = min(stamps)
                row['first_at'] = min(row['first_at'], first) if row['first_at'] else first
            if stamps:
                last = max(stamps)
                row['last_at'] = max(row['last_at'], last) if row['last_at'] else last
    if with_task_rows:
        return totals, result_rows
    return totals


def _task_projection_supported(game):
    """Whether the task-cell projection has an AttemptsInfo contract."""
    definition = get_daily_game(getattr(game, 'id', None))
    return bool(
        definition is None
        or definition.projection_adapter_key == 'attempts_info'
    )


def _canonical_task_results(game, task_group, *, actor_filter=None):
    """Return canonical AttemptsInfo rows grouped by task for task cells.

    This deliberately shares the same SQL/ORM scorer selection as the release
    aggregate.  The task projection is not built for salad state or tournament
    windows until those adapters have an explicit cell-level contract.
    """
    if not _task_projection_supported(game):
        return {}
    from games.results.snapshot import results_attempts_scope_game

    tasks = list(
        Task.objects.visible()
        .filter(task_group=task_group)
        .exclude(task_type='text_with_forms')
        .order_by('number', 'pk')
    )
    if not tasks:
        return {}
    scope_game = results_attempts_scope_game(game, 'general')
    task_ids = [task.pk for task in tasks]
    from games.results_sql_aggregate import get_sql_aggregated_game_actor_rows
    result_rows = get_sql_aggregated_game_actor_rows(
        task_ids,
        game=scope_game,
        actor_filter=actor_filter,
        include_hidden=True,
    )
    return {
        task_id: rows
        for task_id, rows in result_rows.items()
        if rows
    }


def _task_projection_entry(game, task_group, task_id, actor, info):
    """Convert one canonical AttemptsInfo row into a rebuildable cell."""
    if not (info.attempts or info.hint_attempts):
        return None
    actor_data = _projection_actor(actor)
    if not actor_data:
        return None
    user_id = actor_data.pop('user_id', None)
    best_attempt = getattr(info, 'best_attempt', None)
    result_attempt = (
        info.get_result_attempt()
        if callable(getattr(info, 'get_result_attempt', None))
        else best_attempt
    )
    has_pending = any(
        getattr(attempt, 'status', None) == 'Pending'
        for attempt in (info.attempts or [])
    )
    hint_numbers = (
        info.get_hint_numbers()
        if callable(getattr(info, 'get_hint_numbers', None))
        else []
    )
    return DailyTaskResultProjection(
        game=game,
        task_group=task_group,
        task_id=task_id,
        user_id=user_id,
        result_points=info.get_result_points() or 0,
        best_status=getattr(best_attempt, 'status', '') or '',
        best_attempt_at=getattr(result_attempt, 'time', None),
        attempts_count=int(info.get_n_attempts() or 0),
        has_pending=has_pending,
        hint_numbers=list(hint_numbers or []),
        **actor_data,
    )


def _build_task_projection_entries(game, task_group, task_results):
    entries = []
    for task_id, rows in task_results.items():
        for actor, info in rows:
            entry = _task_projection_entry(game, task_group, task_id, actor, info)
            if entry is not None:
                entries.append(entry)
    return entries


def _task_projection_compare_enabled():
    return random.random() < TASK_PROJECTION_COMPARE_SAMPLE_RATE


def compare_task_result_projection(game, task_group, task_ids, actor_types=None):
    """Compare task cells with the canonical scorer and return mismatches.

    The result is intentionally a small diagnostic payload.  It is safe to
    call only after the state is valid; callers can use a non-empty result as
    a signal to fall back to the canonical attempts path for that request.
    """
    canonical_results, canonical_task_rows = _canonical_group_results(
        game, task_group, with_task_rows=True,
    )
    del canonical_results  # The comparison is intentionally cell-scoped.
    canonical_entries = _build_task_projection_entries(
        game, task_group,
        {
            task_id: rows
            for task_id, rows in canonical_task_rows.items()
            if task_id in set(task_ids)
        },
    )
    allowed = set(actor_types or ())

    def signature(item):
        return (
            Decimal(str(item.result_points or 0)),
            item.best_status or '',
            item.best_attempt_at,
            int(item.attempts_count or 0),
            bool(item.has_pending),
            tuple(item.hint_numbers or ()),
        )

    expected = {
        (entry.task_id, entry.actor_type, entry.actor_key): signature(entry)
        for entry in canonical_entries
        if not allowed or entry.actor_type in allowed
    }
    actual = {}
    rows = DailyTaskResultProjection.objects.filter(
        game=game, task_group=task_group, task_id__in=list(task_ids),
    )
    for row in rows:
        if allowed and row.actor_type not in allowed:
            continue
        actual[(row.task_id, row.actor_type, row.actor_key)] = signature(row)

    mismatches = []
    for key in sorted(set(expected) | set(actual), key=str):
        if expected.get(key) != actual.get(key):
            mismatches.append({
                'task_id': key[0],
                'actor_type': key[1],
                'actor_key': key[2],
                'expected': expected.get(key),
                'actual': actual.get(key),
            })
    return mismatches


def load_task_result_projection_rows(game, task_group, task_ids, actor_types=None):
    """Return task-cell rows in the legacy ``(actor, AttemptsInfo)`` shape.

    ``None`` means the projection is not authoritative and callers must use
    the canonical attempts path.  An empty dict is a valid, fully rebuilt
    release with no visible result cells.
    """
    from games.models import DailyResultProjectionState, PersonalResultsParticipant
    from games.results_sql_aggregate import AggregatedAttemptsInfo

    state = DailyResultProjectionState.objects.filter(
        game=game, task_group=task_group,
    ).first()
    if not task_projection_state_is_valid(state, game):
        return None
    if _task_projection_compare_enabled():
        mismatches = compare_task_result_projection(
            game, task_group, task_ids, actor_types=actor_types,
        )
        if mismatches:
            logger.warning(
                'daily_task_result_projection_mismatch game=%s task_group=%s '
                'count=%s sample=%s; using canonical results',
                game.pk, task_group.pk, len(mismatches), mismatches[:3],
            )
            return None
    allowed = set(actor_types or ())
    rows = DailyTaskResultProjection.objects.filter(
        game=game,
        task_group=task_group,
        task_id__in=list(task_ids),
    ).select_related('team', 'user')
    result = defaultdict(list)
    for row in rows:
        if allowed and row.actor_type not in allowed:
            continue
        if row.actor_type == DailyTaskResultProjection.ACTOR_TEAM:
            actor = row.team
            if actor is None or actor.is_hidden:
                continue
        elif row.actor_type == DailyTaskResultProjection.ACTOR_USER:
            actor = PersonalResultsParticipant(user_id=row.user_id)
        else:
            actor = PersonalResultsParticipant(anon_key=row.anon_key)
        result[row.task_id].append((actor, AggregatedAttemptsInfo(
            best_points=row.result_points,
            best_status=row.best_status,
            best_time=row.best_attempt_at,
            n_attempts=row.attempts_count,
            sum_hint_penalty=0,
            hint_numbers=row.hint_numbers or [],
            has_pending=row.has_pending,
        )))
    return result


def load_actor_task_result_points(game, tasks, actor):
    """Return authoritative per-task points for one actor, or ``None``.

    This is the compact read path used by the game progress endpoint.  Every
    represented release must have complete, current task-cell coverage before
    any cells are trusted; callers can then fall back as one unit.
    """
    from games.models import DailyResultProjectionState, DailyTaskResultProjection

    if not game or getattr(game, 'project_id', None) != 'sections':
        return None
    if getattr(game, 'id', None) == 'ladder' or not _task_projection_supported(game):
        return None

    tasks = [task for task in tasks or [] if task is not None and task.pk]
    task_ids = list(dict.fromkeys(task.pk for task in tasks))
    if not task_ids:
        return {}
    groups = {task.task_group_id for task in tasks if task.task_group_id}
    if not groups or any(not task.task_group_id for task in tasks):
        return None

    states = {
        state.task_group_id: state
        for state in DailyResultProjectionState.objects.filter(
            game=game, task_group_id__in=groups,
        )
    }
    if any(
        not task_projection_state_is_valid(states.get(group_id), game)
        for group_id in groups
    ):
        return None

    if actor.team_id is not None:
        actor_type, actor_key = DailyTaskResultProjection.ACTOR_TEAM, str(actor.team_id)
    elif actor.user_id is not None:
        actor_type, actor_key = DailyTaskResultProjection.ACTOR_USER, str(actor.user_id)
    elif actor.anon_key is not None:
        actor_type, actor_key = DailyTaskResultProjection.ACTOR_ANON, str(actor.anon_key)
    else:
        return None

    result = {task_id: (0.0, False, None) for task_id in task_ids}
    rows = DailyTaskResultProjection.objects.filter(
        game=game,
        task_group_id__in=groups,
        task_id__in=task_ids,
        actor_type=actor_type,
        actor_key=actor_key,
    ).values_list('task_id', 'result_points', 'best_status')
    for task_id, points, status in rows:
        result[task_id] = (float(points or 0), True, status or None)

    if _task_projection_compare_enabled():
        from games.scoring import bulk_actor_task_result_points

        canonical = bulk_actor_task_result_points(
            task_ids=task_ids,
            actor=actor,
            mode='general',
            game=game,
            include_other_games=True,
        )
        mismatches = []
        for task_id in task_ids:
            expected = canonical.get(task_id, (0.0, False, None))
            actual = result.get(task_id, (0.0, False, None))
            if (
                round(expected[0], 3) != round(actual[0], 3)
                or bool(expected[1]) != bool(actual[1])
                or (expected[2] or None) != (actual[2] or None)
            ):
                mismatches.append(task_id)
        if mismatches:
            logger.warning(
                'daily_actor_task_projection_mismatch game=%s task_ids=%s; '
                'using canonical progress',
                game.pk, mismatches[:20],
            )
            return canonical
    return result


def load_projection_fallback_durations(game, task_group):
    """Return stored attempt-span durations keyed by canonical actor key."""
    return {
        (row.actor_type, row.actor_key): int(row.fallback_duration_seconds or 0)
        for row in DailyResultProjection.objects.filter(
            game=game, task_group=task_group,
        ).only('actor_type', 'actor_key', 'fallback_duration_seconds')
    }


def _prepublication(game, link, actor, first_at):
    """Use canonical daily start; fall back only to first canonical completion."""
    from games.daily_section import publish_at_for

    publication = publish_at_for(game, link.number)
    if publication is None:
        return False
    user_id = getattr(actor, 'user_id', None)
    anon_key = getattr(actor, 'anon_key', None)
    team_id = actor.pk if isinstance(actor, Team) else None
    if user_id is not None or anon_key or team_id is not None:
        timing = DailySolveTiming.objects.filter(
            game=game, task_group=link.task_group, replay_slot__isnull=True,
        ).filter(user_id=user_id, team__isnull=True, anon_key__isnull=True) if user_id is not None else DailySolveTiming.objects.filter(
            game=game, task_group=link.task_group, replay_slot__isnull=True,
            user__isnull=True, team_id=team_id, anon_key__isnull=True,
        ) if team_id is not None else DailySolveTiming.objects.filter(
            game=game, task_group=link.task_group, replay_slot__isnull=True,
            user__isnull=True, team__isnull=True, anon_key=anon_key,
        )
        started_at = timing.order_by('created_at', 'pk').values_list('created_at', flat=True).first()
    else:
        started_at = None
    return _prepublication_from_times(publication, started_at, first_at)


def _prepublication_from_times(publication, started_at, first_at):
    if publication is None:
        return False
    if started_at is not None:
        return started_at < publication
    return bool(first_at and first_at < publication)


def _timing_starts_for_release(game, task_group):
    starts = {}
    rows = DailySolveTiming.objects.filter(
        game=game, task_group=task_group, replay_slot__isnull=True,
    ).values('team_id', 'user_id', 'anon_key').annotate(first_started_at=Min('created_at'))
    for row in rows:
        key = ('team', row['team_id']) if row['team_id'] is not None else (
            ('user', row['user_id']) if row['user_id'] is not None else ('anon', row['anon_key'])
        )
        if key[1] is not None:
            starts[key] = row['first_started_at']
    return starts


def refresh_daily_result_projection(game, task_group, *, results=None):
    """Idempotently replace one release's projection using the canonical scorer.

    The canonical calculation happens before publication.  The final locked
    transaction publishes it only if no source revision changed meanwhile;
    otherwise the caller can retry without ever creating a false-valid state.
    """
    started_at = monotonic()
    link = GameTaskGroup.objects.filter(game=game, task_group=task_group).first()
    if link is None:
        return 0
    with transaction.atomic():
        state = _state_for_update(game, task_group)
        start_revision = state.source_revision
        state.is_valid = False
        state.full_refresh_required = True
        state.save(update_fields=['is_valid', 'full_refresh_required', 'completed_at'])

    compute_started_at = monotonic()
    if results is None:
        results, canonical_task_rows = _canonical_group_results(
            game, task_group, with_task_rows=True,
        )
    else:
        canonical_task_rows = _canonical_task_results(game, task_group)
    if not _task_projection_supported(game):
        canonical_task_rows = {}
    task_entries = _build_task_projection_entries(
        game, task_group, canonical_task_rows,
    )
    from games.daily_section import publish_at_for

    publication = publish_at_for(game, link.number)
    timing_starts = _timing_starts_for_release(game, task_group)
    entries = []
    for data in results.values():
        actor = data['actor']
        actor_data = _projection_actor(actor)
        if not actor_data or not data['present']:
            continue
        if actor_data['actor_type'] == 'user':
            user_id = actor_data.pop('user_id')
        else:
            user_id = None
        entries.append(DailyResultProjection(
            game=game, task_group=task_group,
            user_id=user_id,
            score=data['score'],
            attempts_count=data['attempts_count'],
            fallback_duration_seconds=elapsed_seconds_between(
                data['first_at'], data['last_at'],
            ),
            is_prepublication=_prepublication_from_times(
                publication, timing_starts.get(actor_key(actor)), data['first_at'],
            ),
            **actor_data,
        ))
    write_started_at = monotonic()
    with transaction.atomic():
        state = _state_for_update(game, task_group)
        if state.source_revision != start_revision:
            logger.info(
                'daily_result_projection_refresh_conflict game=%s task_group=%s '
                'start_revision=%s current_revision=%s',
                game.pk, task_group.pk, start_revision, state.source_revision,
            )
            return 0
        DailyResultProjection.objects.filter(game=game, task_group=task_group).delete()
        DailyResultProjection.objects.bulk_create(entries, batch_size=500)
        DailyTaskResultProjection.objects.filter(
            game=game, task_group=task_group,
        ).delete()
        DailyTaskResultProjection.objects.bulk_create(task_entries, batch_size=500)
        state.adapter_version = scorer_adapter_version(game, task_group)
        state.coverage_complete = True
        state.is_valid = True
        state.full_refresh_required = False
        state.pending_actor_refreshes = 0
        state.completed_at = timezone.now()
        state.save(update_fields=[
            'adapter_version', 'coverage_complete', 'is_valid',
            'full_refresh_required', 'pending_actor_refreshes', 'completed_at',
        ])
        DailyResultProjectionDirtyActor.objects.filter(
            game=game, task_group=task_group, source_revision__lte=start_revision,
        ).delete()
    logger.info(
        'daily_result_projection_refresh game=%s task_group=%s actors=%s task_cells=%s '
        'compute_ms=%.1f write_ms=%.1f total_ms=%.1f',
        game.pk, task_group.pk, len(entries), len(task_entries),
        (write_started_at - compute_started_at) * 1000,
        (monotonic() - write_started_at) * 1000,
        (monotonic() - started_at) * 1000,
    )
    return len(entries)


def schedule_actor_projection(
    game, task_group, *, team=None, user=None, anon_key=None,
    team_id=None, user_id=None,
):
    """Register a durable actor refresh after the canonical transaction commits."""
    if game is None or task_group is None:
        return
    if team is not None or team_id is not None:
        actor_id = team.pk if team is not None else team_id
        actor_filter = {'team_id': actor_id, 'user__isnull': True, 'anon_key__isnull': True}
    elif user is not None or user_id is not None:
        actor_id = getattr(user, 'pk', user) if user is not None else user_id
        actor_filter = {'team__isnull': True, 'user_id': actor_id, 'anon_key__isnull': True}
    elif anon_key:
        actor_filter = {'team__isnull': True, 'user__isnull': True, 'anon_key': str(anon_key)}
    else:
        return
    revision = mark_projection_dirty(
        game, task_group, actor=True, actor_filter=actor_filter,
    )
    if revision is None:
        return
    from games.projection_events import events_enabled, publish_projection_refresh

    if events_enabled():
        transaction.on_commit(
            lambda game_id=game.pk, group_id=task_group.pk:
            publish_projection_refresh(game_id, group_id, mode='actor')
        )
        return
    transaction.on_commit(
        lambda game_id=game.pk, group_id=task_group.pk:
        _refresh_dirty_actors(game_id, group_id)
    )


def schedule_full_projection_refresh(game, task_group):
    """Invalidate a materialized release and rebuild it after the mutation commits.

    Identity transitions can change the actor representation of several rows at
    once.  A targeted actor refresh cannot remove the old identity, so these
    mutations must publish a complete canonical replacement instead.
    """
    revision = mark_projection_dirty(game, task_group, full=True)
    if revision is None:
        return None
    from games.projection_events import events_enabled, publish_projection_refresh

    if events_enabled():
        transaction.on_commit(
            lambda game_id=game.pk, group_id=task_group.pk:
            publish_projection_refresh(game_id, group_id, mode='full')
        )
        return revision

    def run(game_id=game.pk, group_id=task_group.pk):
        from games.models import Game, TaskGroup

        current_game = Game.objects.filter(pk=game_id).first()
        current_group = TaskGroup.objects.filter(pk=group_id).first()
        if current_game is None or current_group is None:
            return
        try:
            refresh_daily_result_projection(current_game, current_group)
        except Exception:
            logger.exception(
                'daily_result_projection_full_refresh_failed game=%s task_group=%s',
                game_id, group_id,
            )
            # mark_projection_dirty has already made the state non-authoritative.
            # Keep it that way if the post-commit repair fails; reconciliation can
            # safely retry the release later.
            with transaction.atomic():
                state = _state_for_update(current_game, current_group)
                state.is_valid = False
                state.full_refresh_required = True
                state.save(update_fields=['is_valid', 'full_refresh_required', 'completed_at'])

    transaction.on_commit(run)
    return revision


def _refresh_actor_by_ids(game_id, group_id, actor_filter, *, expected_revision=None):
    from games.models import Game, TaskGroup

    game = Game.objects.filter(pk=game_id).first()
    group = TaskGroup.objects.filter(pk=group_id).first()
    if game is None or group is None:
        return False
    started_at = monotonic()
    results, canonical_task_rows = _canonical_group_results(
        game, group, actor_filter=actor_filter, with_task_rows=True,
    )
    write_started_at = monotonic()
    projection_actor_identity = _actor_identity_from_filter(actor_filter)
    if not _task_projection_supported(game):
        canonical_task_rows = {}
    task_entries = _build_task_projection_entries(
        game, group, canonical_task_rows,
    )
    try:
        with transaction.atomic():
            state = _state_for_update(game, group)
            identity = _projection_actor_from_filter(actor_filter)
            if not results:
                # Source was removed or is no longer statistical; delete only this actor.
                if identity:
                    DailyResultProjection.objects.filter(game=game, task_group=group, **identity).delete()
                    DailyTaskResultProjection.objects.filter(
                        game=game, task_group=group, **identity,
                    ).delete()
            else:
                link = GameTaskGroup.objects.filter(game=game, task_group=group).first()
                if link is not None:
                    data = next(iter(results.values()))
                    actor_data = _projection_actor(data['actor'])
                    user_id = actor_data.pop('user_id', None)
                    defaults = {
                        'team': actor_data['team'], 'user_id': user_id,
                        'anon_key': actor_data['anon_key'], 'score': data['score'],
                        'attempts_count': data['attempts_count'],
                        'fallback_duration_seconds': elapsed_seconds_between(
                            data['first_at'], data['last_at'],
                        ),
                        'is_prepublication': _prepublication(
                            game, link, data['actor'], data['first_at'],
                        ),
                    }
                    DailyResultProjection.objects.update_or_create(
                        game=game, task_group=group,
                        actor_type=actor_data['actor_type'], actor_key=actor_data['actor_key'],
                        defaults=defaults,
                    )
                if identity:
                    DailyTaskResultProjection.objects.filter(
                        game=game, task_group=group, **identity,
                    ).delete()
                DailyTaskResultProjection.objects.bulk_create(
                    task_entries, batch_size=500,
                )
            if expected_revision is not None and identity:
                actor_identity = _actor_identity_from_filter(actor_filter)
                dirty = DailyResultProjectionDirtyActor.objects.filter(
                    game=game, task_group=group,
                    actor_type=actor_identity[0], actor_key=actor_identity[1],
                    source_revision__lte=expected_revision,
                )
                dirty.delete()
            pending = DailyResultProjectionDirtyActor.objects.filter(
                game=game, task_group=group,
            ).count()
            if (
                expected_revision == state.source_revision
                and state.coverage_complete
                and not state.full_refresh_required
                and pending == 0
            ):
                state.is_valid = True
            state.pending_actor_refreshes = pending
            state.save(update_fields=['pending_actor_refreshes', 'is_valid', 'completed_at'])
        logger.info(
            'daily_result_projection_actor_refresh game=%s task_group=%s '
            'actor_type=%s actor_key=%s task_cells=%s compute_ms=%.1f write_ms=%.1f total_ms=%.1f',
            game_id, group_id,
            projection_actor_identity[0] if projection_actor_identity else 'unknown',
            projection_actor_identity[1] if projection_actor_identity else 'unknown',
            len(task_entries),
            (write_started_at - started_at) * 1000,
            (monotonic() - write_started_at) * 1000,
            (monotonic() - started_at) * 1000,
        )
        return True
    except Exception:
        logger.exception(
            'daily_result_projection_actor_refresh_failed game=%s task_group=%s filter=%s',
            game_id, group_id, actor_filter,
        )
        with transaction.atomic():
            state = _state_for_update(game, group)
            state.is_valid = False
            state.full_refresh_required = True
            state.save(update_fields=[
                'is_valid', 'full_refresh_required', 'completed_at',
            ])
        return False


def _refresh_dirty_actors(game_id, group_id):
    """Refresh a snapshot; newer revisions survive the conditional delete."""
    from games.models import Game, TaskGroup

    rows = list(DailyResultProjectionDirtyActor.objects.filter(
        game_id=game_id, task_group_id=group_id,
    ).values('actor_type', 'actor_key', 'source_revision'))
    for row in rows:
        actor_filter = {
            'team_id': int(row['actor_key']) if row['actor_type'] == 'team' else None,
            'user_id': int(row['actor_key']) if row['actor_type'] == 'user' else None,
            'anon_key': row['actor_key'] if row['actor_type'] == 'anon' else None,
        }
        if not _refresh_actor_by_ids(
            game_id, group_id, actor_filter,
            expected_revision=row['source_revision'],
        ):
            continue
    game = Game.objects.get(pk=game_id)
    group = TaskGroup.objects.get(pk=group_id)
    with transaction.atomic():
        state = _state_for_update(game, group)
        state.pending_actor_refreshes = DailyResultProjectionDirtyActor.objects.filter(
            game_id=game_id, task_group_id=group_id,
        ).count()
        if state.pending_actor_refreshes == 0 and state.coverage_complete and not state.full_refresh_required:
            state.is_valid = True
        state.save(update_fields=['pending_actor_refreshes', 'is_valid', 'completed_at'])


def _projection_actor_from_filter(actor_filter):
    if actor_filter.get('team_id') is not None:
        return {'team_id': actor_filter['team_id']}
    if actor_filter.get('user_id') is not None:
        return {'user_id': actor_filter['user_id']}
    if actor_filter.get('anon_key'):
        return {'anon_key': actor_filter['anon_key']}
    return None
