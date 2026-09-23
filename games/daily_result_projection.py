"""Canonical section score materialization.

Gameplay scoring remains in ``AttemptsInfo`` and the existing task calculators.
This module only turns their output into a rebuildable actor/release projection.
"""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Min

from games.leaderboard import actor_key
from games.models import (
    Attempt, DailyResultProjection, DailyResultProjectionState, GameTaskGroup,
    Task, Team,
)

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
    """Idempotently replace one release's projection using the canonical scorer."""
    link = GameTaskGroup.objects.filter(game=game, task_group=task_group).first()
    if link is None:
        return 0
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
        DailyResultProjection.objects.filter(game=game, task_group=task_group).delete()
        DailyResultProjection.objects.bulk_create(entries, batch_size=500)
        DailyResultProjectionState.objects.update_or_create(
            game=game, task_group=task_group,
            defaults={'adapter_version': scorer_adapter_version(game, task_group)},
        )
    return len(entries)


def schedule_actor_projection(game, task_group, *, team=None, user=None, anon_key=None):
    """Refresh only the actor that changed; keep the full release marker intact."""
    if game is None or task_group is None:
        return
    if team is not None:
        actor_filter = {'team_id': team.pk, 'user__isnull': True, 'anon_key__isnull': True}
    elif user is not None:
        actor_filter = {'team__isnull': True, 'user_id': getattr(user, 'pk', user), 'anon_key__isnull': True}
    elif anon_key:
        actor_filter = {'team__isnull': True, 'user__isnull': True, 'anon_key': str(anon_key)}
    else:
        return
    transaction.on_commit(lambda game_id=game.pk, group_id=task_group.pk, filt=actor_filter: _refresh_actor_by_ids(game_id, group_id, filt))


def _refresh_actor_by_ids(game_id, group_id, actor_filter):
    from games.models import Game, TaskGroup

    game = Game.objects.filter(pk=game_id).first()
    group = TaskGroup.objects.filter(pk=group_id).first()
    if game is None or group is None:
        return
    results = _canonical_group_results(game, group, actor_filter=actor_filter)
    if not results:
        # Source was removed or is no longer statistical; delete only this actor.
        identity = _projection_actor_from_filter(actor_filter)
        if identity:
            DailyResultProjection.objects.filter(game=game, task_group=group, **identity).delete()
        return
    link = GameTaskGroup.objects.filter(game=game, task_group=group).first()
    if link is None:
        # Some gameplay groups (notably historical/non-official salad groups)
        # have no GameTaskGroup release link.  They are not eligible for the
        # daily projection, but their gameplay transaction must still be able
        # to continue into completion analytics.
        return
    data = next(iter(results.values()))
    actor_data = _projection_actor(data['actor'])
    user_id = actor_data.pop('user_id', None)
    defaults = {
        'team': actor_data['team'], 'user_id': user_id,
        'anon_key': actor_data['anon_key'], 'score': data['score'],
        'is_prepublication': _prepublication(
            game, link,
            data['actor'], data['first_at'],
        ),
    }
    DailyResultProjection.objects.update_or_create(
        game=game, task_group=group,
        actor_type=actor_data['actor_type'], actor_key=actor_data['actor_key'],
        defaults=defaults,
    )


def _projection_actor_from_filter(actor_filter):
    if actor_filter.get('team_id'):
        return {'team_id': actor_filter['team_id']}
    if actor_filter.get('user_id'):
        return {'user_id': actor_filter['user_id']}
    if actor_filter.get('anon_key'):
        return {'anon_key': actor_filter['anon_key']}
    return None
