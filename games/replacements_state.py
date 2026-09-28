"""Authoritative actor state for replacements-lines tasks."""

from games.models import ChainTaskState
from games.replacements_lines import (
    parse_replacements_lines_text,
    replacements_line_done_from_state,
)


def current_state(
    task, *, game=None, team=None, user=None, anon_key=None, mode='general',
    replay_slot=None, attempts_info=None,
):
    """Read ChainTaskState, falling back to the latest legacy attempt state."""
    state = None
    if game is not None:
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
            filters = None
        if filters is not None:
            state = ChainTaskState.objects.filter(**filters).values_list(
                'state', flat=True,
            ).first()
    if state is None and attempts_info and attempts_info.attempts:
        state = attempts_info.attempts[-1].state
    return state


def line_done_list(
    task, attempts_info, *, game=None, team=None, user=None, anon_key=None,
    mode='general', replay_slot=None,
):
    """Return solved-line flags for a replacements-lines task and actor."""
    if task.task_type != 'replacements_lines':
        return []
    parsed = parse_replacements_lines_text(
        task.text,
        (task.checker_data or '').strip() or None,
    )
    if not parsed['left_lines']:
        return []
    state = current_state(
        task,
        game=game,
        team=team,
        user=user,
        anon_key=anon_key,
        mode=mode,
        replay_slot=replay_slot,
        attempts_info=attempts_info,
    )
    return replacements_line_done_from_state(state, parsed)
