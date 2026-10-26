"""Shared lookup rules for actor-scoped ChainTaskState rows."""

from games.models import Attempt, ChainTaskState


def actor_identity_kwargs(actor):
    """Translate a result-row actor into nullable actor fields."""
    if getattr(actor, 'is_team_results_row', False):
        return {'team': actor, 'user': None, 'anon_key': None}
    if getattr(actor, 'user_id', None) is not None:
        return {'team': None, 'user_id': actor.user_id, 'anon_key': None}
    if getattr(actor, 'anon_key', None):
        return {'team': None, 'user': None, 'anon_key': actor.anon_key}
    return None


def chain_state_for_actor(task, game, *, team=None, user=None, anon_key=None,
                          mode='general', replay_slot=None):
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


def chain_state_for_result_actor(game, task, actor):
    """Return the official state row for a results-table actor."""
    if actor is None or task is None:
        return None
    identity = actor_identity_kwargs(actor)
    if identity is None:
        return None
    if 'user_id' in identity:
        return ChainTaskState.objects.filter(
            task=task, game=game, game_mode='general',
            replay_slot__isnull=True, user_id=identity['user_id'],
            team__isnull=True, anon_key__isnull=True,
        ).first()
    return chain_state_for_actor(
        task, game, mode='general', replay_slot=None, **identity,
    )


def latest_attempt_state_for_result_actor(game, task, actor):
    """Return the latest non-empty official attempt state for an actor."""
    if actor is None or task is None:
        return None
    identity = actor_identity_kwargs(actor)
    if identity is None:
        return None
    filters = {
        'task': task, 'game': game,
        'replay_slot__isnull': True, 'skip': False,
    }
    if 'user_id' in identity:
        filters.update(
            user_id=identity['user_id'],
            team__isnull=True,
            anon_key__isnull=True,
        )
    else:
        filters.update(**identity)
        if identity.get('team') is not None:
            filters.update(user__isnull=True, anon_key__isnull=True)
        elif identity.get('anon_key') is not None:
            filters.update(team__isnull=True, user__isnull=True)
    return Attempt.manager.filter(**filters).exclude(
        state__isnull=True,
    ).exclude(state='').order_by('-time').values_list('state', flat=True).first()
