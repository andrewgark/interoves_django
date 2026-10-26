"""Durable, deduplicated replay planning for gameplay task changes."""

from collections import defaultdict

from games.models import Attempt, CHAIN_TASK_TYPES, ChainTaskState, Game, Task


def actor_key(attempt):
    return (
        attempt.team_id,
        attempt.user_id,
        attempt.anon_key or None,
        attempt.replay_slot_id,
    )


def _actor_groups(task_ids, *, selected_keys=None):
    groups = defaultdict(set)
    rows = Attempt.manager.filter(
        task_id__in=task_ids,
        task__task_type__in=CHAIN_TASK_TYPES,
        game_id__isnull=False,
    ).values_list('task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id')
    for task_id, game_id, team_id, user_id, anon_key, replay_slot_id in rows:
        key = (team_id, user_id, anon_key or None, replay_slot_id)
        if selected_keys is None or (task_id, game_id, key) in selected_keys:
            groups[(task_id, game_id)].add(key)

    # ChainTaskState can outlive Attempt rows after an identity migration.
    rows = ChainTaskState.objects.filter(
        task_id__in=task_ids,
        task__task_type__in=CHAIN_TASK_TYPES,
        game_id__isnull=False,
    ).exclude(state__isnull=True).exclude(state='').values_list(
        'task_id', 'game_id', 'team_id', 'user_id', 'anon_key', 'replay_slot_id',
    )
    for task_id, game_id, team_id, user_id, anon_key, replay_slot_id in rows:
        key = (team_id, user_id, anon_key or None, replay_slot_id)
        if selected_keys is None or (task_id, game_id, key) in selected_keys:
            groups[(task_id, game_id)].add(key)
    return groups


def queue_chain_replays_for_tasks(
    task_ids, *, selected_keys=None, pending_resolution=None, return_receipt=False,
):
    """Create one durable replay job per task/game, with unique actors."""
    from games.word_salad_recheck import enqueue_actor_rechecks

    task_ids = tuple(sorted({int(task_id) for task_id in task_ids if task_id}))
    receipts = []
    for (task_id, game_id), actor_keys in _actor_groups(
        task_ids, selected_keys=selected_keys,
    ).items():
        result = enqueue_actor_rechecks(
            task=Task.objects.get(pk=task_id),
            game=Game.objects.get(pk=game_id),
            actors=sorted(actor_keys, key=str),
            pending_resolution=(pending_resolution or {}).get(str(task_id), {}),
            return_receipt=return_receipt,
        )
        if return_receipt:
            receipts.append(result)
    return receipts if return_receipt else None
