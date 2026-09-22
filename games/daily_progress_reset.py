"""Reset progress accidentally created before the current daily release."""

from __future__ import annotations

from collections import defaultdict

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from games.daily_section import current_number_for, publish_at_for, schedule_for
from games.models import (
    Attempt, ChainTaskState, DailyGameDifficulty, DailyResultProjection,
    DailyResultProjectionState, DailySolveTiming, Game, GameTaskGroup,
    HintAttempt, PlayerCompletedGame, PlayerStartedGame, ReplaySlot, Task, Team,
)


DAILY_RESET_GAME_IDS = ('ladder', 'alphabetty', 'salad')
CHAIN_TYPES = {'wall', 'replacements_lines', 'raddle', 'alphabetty', 'word_salad'}


def _actor_key(team_id, user_id, anon_key, replay_slot_id=None):
    return team_id, user_id, anon_key or None, replay_slot_id


def _actor_objects(keys):
    teams = Team.objects.in_bulk({key[0] for key in keys if key[0]})
    users = User.objects.in_bulk({key[1] for key in keys if key[1]})
    slots = ReplaySlot.objects.in_bulk({key[3] for key in keys if key[3]})
    for team_id, user_id, anon_key, replay_slot_id in keys:
        team = teams.get(team_id) if team_id else None
        user = users.get(user_id) if user_id else None
        slot = slots.get(replay_slot_id) if replay_slot_id else None
        if (team_id and team is None) or (user_id and user is None) or (replay_slot_id and slot is None):
            continue
        yield team, user, anon_key, slot


def _rebuild_chain(task, game, team, user, anon_key, replay_slot):
    from games.recheck import recheck_chain_task, recheck_word_salad_actor

    kwargs = dict(task=task, game=game, team=team, user=user, anon_key=anon_key,
                  replay_slot=replay_slot, notify=False)
    if task.task_type == 'word_salad':
        recheck_word_salad_actor(**kwargs)
    else:
        recheck_chain_task(**kwargs)


@transaction.atomic
def reset_daily_release_progress(placement, *, now=None):
    """Delete official and replay attempts before publication and rebuild state."""
    now = now or timezone.now()
    placement = GameTaskGroup.objects.select_for_update().select_related(
        'game', 'task_group',
    ).get(pk=placement.pk)
    published_at = publish_at_for(placement.game, placement.number)
    if published_at is None or published_at > now:
        return {'reset': False, 'attempts': 0, 'hint_attempts': 0, 'chains': 0}

    tasks = list(Task.objects.filter(task_group=placement.task_group))
    task_ids = [task.pk for task in tasks]
    if not task_ids:
        return {'reset': False, 'attempts': 0, 'hint_attempts': 0, 'chains': 0}

    old_attempts = Attempt.manager.filter(
        game=placement.game, task_id__in=task_ids, time__lt=published_at,
    )
    old_hint_attempts = HintAttempt.objects.filter(
        hint__task_id__in=task_ids, time__lt=published_at,
    )
    stale_timing = DailySolveTiming.objects.filter(
        game=placement.game, task_group=placement.task_group,
        created_at__lt=published_at,
    )
    stale_started = PlayerStartedGame.objects.filter(
        game=placement.game, task_group=placement.task_group,
        started_at__lt=published_at,
    )
    stale_completed = PlayerCompletedGame.objects.filter(
        game=placement.game, task_group=placement.task_group,
        completed_at__lt=published_at,
    )
    stale_states = ChainTaskState.objects.filter(
        game=placement.game, task_id__in=task_ids, updated_at__lt=published_at,
    )
    stale_projection = DailyResultProjection.objects.filter(
        game=placement.game, task_group=placement.task_group,
    )
    if not any(qs.exists() for qs in (
        old_attempts, old_hint_attempts, stale_timing, stale_started,
        stale_completed, stale_states, stale_projection,
    )):
        return {'reset': False, 'attempts': 0, 'hint_attempts': 0, 'chains': 0}

    chain_ids = {task.pk for task in tasks if task.task_type in CHAIN_TYPES}
    actors = defaultdict(set)
    for task_id, team_id, user_id, anon_key, replay_slot_id in old_attempts.values_list(
        'task_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
    ):
        if task_id in chain_ids:
            actors[task_id].add(_actor_key(team_id, user_id, anon_key, replay_slot_id))
    for task_id, team_id, user_id, anon_key, replay_slot_id in stale_states.values_list(
        'task_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
    ):
        if task_id in chain_ids:
            actors[task_id].add(_actor_key(team_id, user_id, anon_key, replay_slot_id))

    counts = {'attempts': old_attempts.count(), 'hint_attempts': old_hint_attempts.count()}
    old_attempts.delete()
    old_hint_attempts.delete()
    stale_timing.delete()
    stale_started.delete()
    stale_completed.delete()
    stale_projection.delete()
    DailyResultProjectionState.objects.filter(
        game=placement.game, task_group=placement.task_group,
    ).delete()
    DailyGameDifficulty.objects.filter(placement=placement).update(
        data_revision=F('data_revision') + 1, dirty=True,
    )

    task_by_id = {task.pk: task for task in tasks}
    rebuilt = 0
    for task_id, actor_keys in actors.items():
        for team, user, anon_key, replay_slot in _actor_objects(actor_keys):
            ChainTaskState.objects.filter(
                task_id=task_id, game=placement.game,
                team=team, user=user, anon_key=anon_key, replay_slot=replay_slot,
            ).delete()
            _rebuild_chain(task_by_id[task_id], placement.game, team, user, anon_key, replay_slot)
            rebuilt += 1
    return {'reset': True, **counts, 'chains': rebuilt}


def reset_current_daily_release_progress(*, now=None, game_ids=DAILY_RESET_GAME_IDS):
    now = now or timezone.now()
    game_ids = tuple(game_ids) if game_ids else DAILY_RESET_GAME_IDS
    results = []
    for game in Game.objects.filter(project_id='sections', id__in=game_ids):
        if schedule_for(game.id) is None:
            continue
        number = current_number_for(game, now=now)
        placement = GameTaskGroup.objects.filter(game=game, number=str(number)).first() if number else None
        if placement is None:
            continue
        result = reset_daily_release_progress(placement, now=now)
        if result['reset']:
            results.append({'game_id': game.id, 'number': str(number), **result})
    return results
