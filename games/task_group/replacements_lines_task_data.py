"""Replacements-lines task payloads for task-group UI."""

import json

from games.puzzles.replacements.lines import (
    parse_replacements_lines_text,
    replacements_solved_slots_from_state,
)
from games.task_group.replacements_state import current_state


def replacements_lines_data_by_task_id(
    tasks,
    attempts_info_by_task_id,
    *,
    game,
    team,
    user,
    anon_key,
    mode,
    replay_slot=None,
) -> dict:
    """Build per-task replacements-lines render context keyed by task id."""
    out = {}
    for t in tasks:
        if t.task_type != 'replacements_lines':
            continue
        parsed = parse_replacements_lines_text(t.text, (t.checker_data or '').strip() or None)
        n_lines = len(parsed['left_lines'])
        line_solved = [False] * n_lines
        line_attempts = [0] * n_lines
        answers_by_line = parsed.get('answers', [])
        line_done = [False] * n_lines
        ai = attempts_info_by_task_id.get(t.id)
        if ai and ai.attempts:
            for a in ai.attempts:
                try:
                    p = json.loads(a.text)
                    idx = int(p.get('line_index', -1))
                    if 0 <= idx < n_lines:
                        line_attempts[idx] += 1
                except (ValueError, TypeError):
                    pass
        # ChainTaskState is authoritative. Do not reconstruct a solved line by
        # combining slots from different attempts.
        state = current_state(
            t, game=game, team=team, user=user, anon_key=anon_key,
            mode=mode, replay_slot=replay_slot, attempts_info=ai,
        )
        solved_slots_from_state = replacements_solved_slots_from_state(state, parsed)
        for i in range(n_lines):
            solved = solved_slots_from_state.get(i, set())
            line_done[i] = bool(answers_by_line[i]) and len(solved) == len(answers_by_line[i])
        slot_counts = [len(answers_by_line[i]) for i in range(n_lines)]
        out[t.id] = {
            'parsed': parsed,
            'line_solved': line_solved,
            'line_done': line_done,
            'line_attempts': line_attempts,
            'slot_correct': [
                [j in solved_slots_from_state.get(i, set()) for j in range(len(answers_by_line[i]))]
                for i in range(n_lines)
            ],
            'n_lines': n_lines,
            'slot_counts': slot_counts,
            'max_attempts': t.get_max_attempts(),
            'max_points_total': t.get_results_max_points(),
        }
    return out
