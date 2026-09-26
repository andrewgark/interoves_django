"""Canonical section score materialization.

Gameplay scoring remains in ``AttemptsInfo`` and the existing task calculators.
This module only turns their output into a rebuildable actor/release projection.
"""
from collections import defaultdict
from decimal import Decimal

import logging

from django.db import IntegrityError, transaction
from django.db.models import F, Min
from django.utils import timezone

from games.leaderboard import actor_key
from games.models import (
    Attempt, DailyResultProjection, DailyResultProjectionState, GameTaskGroup,
    Task, Team,
)

logger = logging.getLogger('application')

# Bump when canonical score adaptation semantics change; old releases then
# transparently return to the canonical full-compute path until rebuilt.
# Increment deliberately when score semantics in an adapter change. These are
# semantic contracts, not source hashes: a change in AttemptsInfo scoring,
# hint penalties, or Salad state scoring requires an explicit version bump.
SCORER_ADAPTER_VERSIONS = {
    'attempts_info': 1,
    'salad_state': 1,
}


def scorer_adapter_version(game, task_group=None):
    from games.word_salad import WORD_SALAD_GAME_ID
    adapter = 'salad_state' if str(getattr(game, 'id', '')) == WORD_SALAD_GAME_ID else 'attempts_info'
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


def mark_projection_dirty(game, task_group, *, actor=False, full=False):
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
        if actor:
            state.pending_actor_refreshes = F('pending_actor_refreshes') + 1
        state.save(update_fields=[
            'source_revision', 'is_valid', 'full_refresh_required',
            'pending_actor_refreshes', 'completed_at',
        ])
        state.refresh_from_db(fields=['source_revision', 'pending_actor_refreshes'])
        return state.source_revision


def _canonical_group_results(game, task_group, *, actor_filter=None):
    """Return exact existing AttemptsInfo scores for the release, grouped by actor."""
    from games.results_snapshot import results_attempts_scope_game

    tasks = list(Task.objects.visible().filter(task_group=task_group).exclude(task_type='text_with_forms').order_by('number', 'pk'))
    if not tasks:
        return {}
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
    first_times = {}
    if not tasks_need_orm_results_aggregate(tasks):
        attempt_qs = Attempt.manager.filter(task_id__in=task_ids, skip=False, replay_slot__isnull=True)
        if scope_game is not None:
            attempt_qs = attempt_qs.filter(game=scope_game)
        if actor_filter:
            attempt_qs = attempt_qs.filter(**actor_filter)
        for source in attempt_qs.values('team_id', 'user_id', 'anon_key').annotate(first_at=Min('time')):
            key = ('team', source['team_id']) if source['team_id'] is not None else (
                ('user', source['user_id']) if source['user_id'] is not None else ('anon', source['anon_key'])
            )
            if key[1] is not None:
                first_times[key] = source['first_at']
    totals = defaultdict(lambda: {'actor': None, 'score': Decimal('0'), 'first_at': None, 'present': False})
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
            stamps = [a.time for a in (info.attempts or ()) if getattr(a, 'time', None)]
            if key in first_times:
                first = first_times[key]
                row['first_at'] = min(row['first_at'], first) if row['first_at'] else first
            elif stamps:
                first = min(stamps)
                row['first_at'] = min(row['first_at'], first) if row['first_at'] else first
    return totals


def _prepublication(game, link, actor, first_at):
    """Use canonical daily start; fall back only to first canonical completion."""
    from games.daily_section import publish_at_for
    from games.models import DailySolveTiming

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
        started_at = timing.values_list('created_at', flat=True).first()
        if started_at is not None:
            return started_at < publication
    return bool(first_at and first_at < publication)


def refresh_daily_result_projection(game, task_group, *, results=None):
    """Idempotently replace one release's projection using the canonical scorer.

    The canonical calculation happens before publication.  The final locked
    transaction publishes it only if no source revision changed meanwhile;
    otherwise the caller can retry without ever creating a false-valid state.
    """
    link = GameTaskGroup.objects.filter(game=game, task_group=task_group).first()
    if link is None:
        return 0
    with transaction.atomic():
        state = _state_for_update(game, task_group)
        start_revision = state.source_revision
        state.is_valid = False
        state.full_refresh_required = True
        state.save(update_fields=['is_valid', 'full_refresh_required', 'completed_at'])

    results = results if results is not None else _canonical_group_results(game, task_group)
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
            is_prepublication=_prepublication(game, link, actor, data['first_at']),
            **actor_data,
        ))
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
    revision = mark_projection_dirty(game, task_group, actor=True)
    if revision is None:
        return
    from games.projection_events import events_enabled, publish_projection_refresh

    if events_enabled():
        transaction.on_commit(
            lambda game_id=game.pk, group_id=task_group.pk, filt=actor_filter, rev=revision:
            publish_projection_refresh(
                game_id, group_id, mode='actor', actor_filter=filt, revision=rev,
            )
        )
        return
    transaction.on_commit(
        lambda game_id=game.pk, group_id=task_group.pk, filt=actor_filter, rev=revision:
        _refresh_actor_by_ids(game_id, group_id, filt, expected_revision=rev)
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
        return
    results = _canonical_group_results(game, group, actor_filter=actor_filter)
    try:
        with transaction.atomic():
            state = _state_for_update(game, group)
            identity = _projection_actor_from_filter(actor_filter)
            if not results:
                # Source was removed or is no longer statistical; delete only this actor.
                if identity:
                    DailyResultProjection.objects.filter(game=game, task_group=group, **identity).delete()
            else:
                link = GameTaskGroup.objects.filter(game=game, task_group=group).first()
                if link is not None:
                    data = next(iter(results.values()))
                    actor_data = _projection_actor(data['actor'])
                    user_id = actor_data.pop('user_id', None)
                    defaults = {
                        'team': actor_data['team'], 'user_id': user_id,
                        'anon_key': actor_data['anon_key'], 'score': data['score'],
                        'is_prepublication': _prepublication(
                            game, link, data['actor'], data['first_at'],
                        ),
                    }
                    DailyResultProjection.objects.update_or_create(
                        game=game, task_group=group,
                        actor_type=actor_data['actor_type'], actor_key=actor_data['actor_key'],
                        defaults=defaults,
                    )
            state.pending_actor_refreshes = max(0, int(state.pending_actor_refreshes or 0) - 1)
            if (
                expected_revision == state.source_revision
                and state.coverage_complete
                and not state.full_refresh_required
                and state.pending_actor_refreshes == 0
            ):
                state.is_valid = True
            state.save(update_fields=['pending_actor_refreshes', 'is_valid', 'completed_at'])
    except Exception:
        logger.exception(
            'daily_result_projection_actor_refresh_failed game=%s task_group=%s filter=%s',
            game_id, group_id, actor_filter,
        )
        with transaction.atomic():
            state = _state_for_update(game, group)
            state.pending_actor_refreshes = max(0, int(state.pending_actor_refreshes or 0) - 1)
            state.is_valid = False
            state.full_refresh_required = True
            state.save(update_fields=[
                'pending_actor_refreshes', 'is_valid', 'full_refresh_required', 'completed_at',
            ])


def _projection_actor_from_filter(actor_filter):
    if actor_filter.get('team_id'):
        return {'team_id': actor_filter['team_id']}
    if actor_filter.get('user_id'):
        return {'user_id': actor_filter['user_id']}
    if actor_filter.get('anon_key'):
        return {'anon_key': actor_filter['anon_key']}
    return None
