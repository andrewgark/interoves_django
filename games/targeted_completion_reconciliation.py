"""Targeted completion repair for exceptional content mutations.

This module deliberately works from one game/task-group pair and the actors
already attached to that pair.  It is not a replacement for the one-time
legacy migration and never queues analytics goals.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction

from games.models import (
    Attempt,
    ChainTaskState,
    Game,
    PlayerCompletedGame,
    Task,
    Team,
    TaskGroup,
)


def _actor_key(team_id, user_id, anon_key):
    return team_id, user_id, anon_key or None


def completion_pairs_for_actor(*, user=None, anon_key=None):
    """Return only game/task-group pairs touched by one actor identity."""
    keys = {}
    if user is not None:
        keys['user'] = user
    if anon_key is not None:
        keys['anon_key'] = anon_key
    if not keys:
        return set()
    pairs = set(
        Attempt.manager.filter(**keys, team__isnull=True)
        .values_list('game_id', 'task__task_group_id')
    )
    pairs.update(
        ChainTaskState.objects.filter(**keys, team__isnull=True)
        .values_list('game_id', 'task__task_group_id')
    )
    pairs.update(
        PlayerCompletedGame.objects.filter(**keys, team__isnull=True)
        .values_list('game_id', 'task_group_id')
    )
    return {(game_id, group_id) for game_id, group_id in pairs if game_id and group_id}


def _actor_keys_for_group(*, game_id, task_group_id):
    task_ids = list(Task.objects.filter(task_group_id=task_group_id).values_list('id', flat=True))
    keys = set()
    filters = (
        Attempt.manager.filter(game_id=game_id, task_id__in=task_ids, replay_slot__isnull=True),
        ChainTaskState.objects.filter(game_id=game_id, task_id__in=task_ids, replay_slot__isnull=True),
        PlayerCompletedGame.objects.filter(
            game_id=game_id, task_group_id=task_group_id,
        ),
    )
    for queryset in filters:
        keys.update(
            _actor_key(team_id, user_id, anon_key)
            for team_id, user_id, anon_key in queryset.values_list(
                'team_id', 'user_id', 'anon_key',
            )
        )
    return keys


def _resolve_actor(key):
    team_id, user_id, anon_key = key
    team = Team.objects.filter(pk=team_id).first() if team_id else None
    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    if team_id and team is None:
        return None
    if user_id and user is None:
        return None
    return team, user, anon_key


def _rebuild_actor_task(task, *, game, team, user, anon_key):
    if task.task_type not in ('wall', 'replacements_lines', 'raddle', 'alphabetty', 'word_salad'):
        return
    from games.recheck import recheck_chain_task, recheck_word_salad_actor

    kwargs = {
        'task': task,
        'game': game,
        'team': team,
        'user': user,
        'anon_key': anon_key,
        'notify': False,
    }
    if task.task_type == 'word_salad':
        recheck_word_salad_actor(**kwargs)
    else:
        recheck_chain_task(**kwargs)


def reconcile_task_group_actors(
    *,
    game_id,
    task_group_id,
    actor_keys=None,
    rebuild_task_id=None,
    delete_instance_id=None,
):
    """Reconcile only actors attached to one affected game/task-group.

    ``rebuild_task_id`` is used for checker/data changes where the current
    ChainTaskState must first be replayed.  ``delete_instance_id`` is used when
    a mapping is removed and the old canonical instance is no longer valid.
    """
    game = Game.objects.filter(pk=game_id).first()
    task_group = TaskGroup.objects.filter(pk=task_group_id).first()
    keys = set(actor_keys or ())
    if game is None:
        return 0
    if task_group is not None:
        keys.update(_actor_keys_for_group(game_id=game_id, task_group_id=task_group_id))
    if not keys:
        return 0

    rebuild = Task.objects.filter(pk=rebuild_task_id).first() if rebuild_task_id else None
    if rebuild is not None and task_group is None:
        rebuild = None

    from games.analytics import reconcile_completed_game_after_recheck

    changed = 0
    for key in keys:
        actor = _resolve_actor(key)
        if actor is None:
            continue
        team, user, anon_key = actor
        if delete_instance_id:
            changed += PlayerCompletedGame.objects.filter(
                game_instance_id=delete_instance_id,
                team=team,
                user=user,
                anon_key=anon_key,
            ).delete()[0]
            continue
        if rebuild is not None:
            _rebuild_actor_task(
                rebuild, game=game, team=team, user=user, anon_key=anon_key,
            )
        if task_group is None:
            continue
        task = Task.objects.filter(task_group=task_group).order_by('id').first()
        if task is None:
            changed += PlayerCompletedGame.objects.filter(
                game_id=game_id,
                task_group_id=task_group_id,
                team=team,
                user=user,
                anon_key=anon_key,
            ).delete()[0]
            continue
        if reconcile_completed_game_after_recheck(
            task=task,
            game=game,
            team=team,
            user=user,
            anon_key=anon_key,
        ):
            changed += 1
    return changed


def schedule_task_semantics_reconciliation(
    *,
    old_group_id,
    new_group_id,
    game_ids,
    actor_keys,
    rebuild_task_id=None,
):
    """Schedule a post-commit targeted reconciliation for Task mutations."""
    pairs = {(game_id, group_id) for game_id in game_ids for group_id in (old_group_id, new_group_id) if group_id}

    def run():
        for game_id, group_id in pairs:
            reconcile_task_group_actors(
                game_id=game_id,
                task_group_id=group_id,
                actor_keys=actor_keys,
                rebuild_task_id=rebuild_task_id if group_id == new_group_id else None,
            )

    transaction.on_commit(run)


def schedule_mapping_reconciliation(*, old, instance, actor_keys):
    """Keep PCG aligned when a GameTaskGroup link is created/changed/deleted."""
    old_instance_id = ''
    if old is not None:
        old_instance_id = '{}:{}'.format(old.game_id, old.task_group_id)
    def run():
        if old is not None and (
            instance is None
            or old.game_id != instance.game_id
            or old.task_group_id != instance.task_group_id
        ):
            reconcile_task_group_actors(
                game_id=old.game_id,
                task_group_id=old.task_group_id,
                actor_keys=actor_keys,
                delete_instance_id=old_instance_id,
            )
        if instance is not None:
            reconcile_task_group_actors(
                game_id=instance.game_id,
                task_group_id=instance.task_group_id,
                actor_keys=actor_keys,
            )

    transaction.on_commit(run)
