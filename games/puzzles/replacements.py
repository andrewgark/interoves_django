"""Замены: one posted line. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.base import Puzzle
from games.puzzles.move import MoveRefusal, TextMove


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


class ReplacementsPuzzle(Puzzle):
    task_type = 'replacements_lines'

    parse_move = staticmethod(parse_move)
