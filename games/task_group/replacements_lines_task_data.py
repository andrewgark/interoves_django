"""Replacements-lines task payloads for task-group UI."""

import json

from games.puzzles.replacements.lines import (
    parse_replacements_lines_text,
    replacements_solved_slots_from_state,
)
from games.puzzles.replacements.lines.ui_state import clean_guesses, load_guesses, slot_key
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
        guesses, ui_revision = load_guesses(
            t, game=game, mode=mode, team=team, user=user, anon_key=anon_key,
            replay_slot=replay_slot,
        )
        guesses = clean_guesses(guesses, slot_counts, solved_slots_from_state)
        lines = []
        for i, tokens in enumerate(parsed['right_tokens']):
            solved = solved_slots_from_state.get(i, set())
            items = []
            for token in tokens:
                if token['type'] != 'slot':
                    items.append(token)
                    continue
                j = token['slot_index']
                key = slot_key(i, j)
                correct = j in solved
                items.append({
                    'type': 'slot',
                    'slot_index': j,
                    'key': key,
                    'encoded': token.get('encoded', ''),
                    'correct': correct,
                    # Only solved slots reveal their answer to the page.
                    'answer': answers_by_line[i][j] if correct else '',
                    'guess': '' if correct else guesses.get(key, ''),
                })
            lines.append({
                'index': i,
                'tokens': items,
                'done': line_done[i],
                'attempts': line_attempts[i],
                'slot_count': slot_counts[i],
            })
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
            'lines': lines,
            'guesses_json': json.dumps(guesses, ensure_ascii=False),
            'ui_revision': ui_revision,
            'slot_counts': slot_counts,
            'max_attempts': t.get_max_attempts(),
            'max_points_total': t.get_results_max_points(),
        }
    return out
