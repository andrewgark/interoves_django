"""Замены: one posted line. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.base import Puzzle
from games.puzzles.duplicate import same_text_verdict
from games.puzzles.move import MoveRefusal, TextMove
from games.puzzles.progress import _int_tuple, _slot_pairs
from games.puzzles.quota import json_field_quota


def parse_move(post):
    """Read a replacements POST. Statuses are the existing JSON codes."""
    try:
        line_index = int(post.get('line_index', 0))
    except (TypeError, ValueError):
        return MoveRefusal('invalid_form')
    answers_raw = post.get('answers')
    if answers_raw is not None:
        try:
            answers = json.loads(answers_raw)
        except (ValueError, TypeError):
            answers = post.getlist('answers[]')
    else:
        answers = post.getlist('answers[]')
    if not isinstance(answers, list):
        return MoveRefusal('invalid_form')
    answers = list(answers)
    if not answers or all(str(answer).strip() == '' for answer in answers):
        return MoveRefusal('empty')
    return TextMove(json.dumps({'line_index': line_index, 'answers': answers}))


def attempt_limit_message(attempt, revision_attempts, task, team, *, attempts=None, chain_state=None):
    """Tournament quota for one replacements line. None means the line still has tries."""
    return json_field_quota(
        attempt, revision_attempts, task, team,
        field='line_index', label='line',
    )


def blocks_duplicate(attempt_text, other_text, *, task, state_raw, other_attempt=None):
    return same_text_verdict(attempt_text, other_text, other_attempt)


def progress_signature(state):
    """Solved replacement cells, or whole lines when no cell map is stored."""
    slots = state.get('solved_slots')
    if isinstance(slots, dict) and slots:
        return ('slots', _slot_pairs(slots))
    return ('lines', _int_tuple(state.get('solved_lines')))


def results_max_points(task, m):
    """Green-cell maximum: answer rows times the task multiplier."""
    n = task.replacements_answer_rows
    if n is None:
        n = task._replacements_lines_n_answer_rows()
    if n > 0:
        return m * n
    return m


class ReplacementsPuzzle(Puzzle):
    task_type = 'replacements_lines'
    checker_id = 'replacements_lines'

    parse_move = staticmethod(parse_move)
    attempt_limit_message = staticmethod(attempt_limit_message)
    blocks_duplicate = staticmethod(blocks_duplicate)
    progress_signature = staticmethod(progress_signature)
    results_max_points = staticmethod(results_max_points)
