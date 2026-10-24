"""Замены: one posted line. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json
from dataclasses import dataclass

from games.puzzles.base import Puzzle


@dataclass(frozen=True)
class ReplacementsMove:
    text: str


@dataclass(frozen=True)
class ReplacementsMoveRefusal:
    status: str


def parse_move(post):
    """Read a replacements POST. Statuses are the existing JSON codes."""
    try:
        line_index = int(post.get('line_index', 0))
    except (TypeError, ValueError):
        return ReplacementsMoveRefusal('invalid_form')
    answers_raw = post.get('answers')
    if answers_raw is not None:
        try:
            answers = json.loads(answers_raw)
        except (ValueError, TypeError):
            answers = post.getlist('answers[]')
    else:
        answers = post.getlist('answers[]')
    if not isinstance(answers, list):
        return ReplacementsMoveRefusal('invalid_form')
    answers = list(answers)
    if not answers or all(str(answer).strip() == '' for answer in answers):
        return ReplacementsMoveRefusal('empty')
    return ReplacementsMove(json.dumps({'line_index': line_index, 'answers': answers}))


class ReplacementsPuzzle(Puzzle):
    task_type = 'replacements_lines'

    parse_move = staticmethod(parse_move)
