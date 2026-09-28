"""Authoritative actor state for replacements-lines tasks."""

from games.tasks.actor_state import chain_state_for_actor
from games.replacements_lines import parse_replacements_lines_text, replacements_line_done_from_state


def current_state(task, *, game=None, team=None, user=None, anon_key=None,
                  mode='general', replay_slot=None, attempts_info=None):
    state = None
    if game is not None:
        row = chain_state_for_actor(
            task, game, team=team, user=user, anon_key=anon_key,
            mode=mode, replay_slot=replay_slot,
        )
        state = row.state if row is not None else None
    if state is None and attempts_info and attempts_info.attempts:
        state = attempts_info.attempts[-1].state
    return state


def line_done_list(task, attempts_info, *, game=None, team=None, user=None,
                   anon_key=None, mode='general', replay_slot=None):
    if task.task_type != 'replacements_lines':
        return []
    parsed = parse_replacements_lines_text(
        task.text, (task.checker_data or '').strip() or None,
    )
    if not parsed['left_lines']:
        return []
    state = current_state(
        task, game=game, team=team, user=user, anon_key=anon_key,
        mode=mode, replay_slot=replay_slot, attempts_info=attempts_info,
    )
    return replacements_line_done_from_state(state, parsed)
