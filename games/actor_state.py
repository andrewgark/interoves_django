"""Shared lookup rules for actor-scoped ChainTaskState rows."""

from games.models import ChainTaskState


def chain_state_for_actor(
    task,
    game,
    *,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    replay_slot=None,
):
    """Return the authoritative state row for one actor and task."""
    filters = {
        'task': task,
        'game': game,
        'game_mode': 'tournament' if mode == 'tournament' else 'general',
        'replay_slot': replay_slot,
    }
    if team is not None:
        filters.update(team=team, user__isnull=True, anon_key__isnull=True)
    elif user is not None:
        filters.update(user=user, team__isnull=True, anon_key__isnull=True)
    elif anon_key is not None:
        filters.update(anon_key=anon_key, team__isnull=True, user__isnull=True)
    else:
        return None
    return ChainTaskState.objects.filter(**filters).first()
