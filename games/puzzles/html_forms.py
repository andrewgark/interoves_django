"""HTML-формы: one posted field. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json
from dataclasses import dataclass

from games.puzzles.base import Puzzle


@dataclass(frozen=True)
class HtmlFormsMove:
    text: str


@dataclass(frozen=True)
class HtmlFormsMoveRefusal:
    status: str


def parse_move(post):
    """Read an html-forms POST. Statuses are the existing JSON codes."""
    form_key = str(post.get('form_key') or '').strip()
    answer_text = (post.get('text') or '').strip()
    if not form_key:
        return HtmlFormsMoveRefusal('invalid_form')
    if not answer_text:
        return HtmlFormsMoveRefusal('empty')
    return HtmlFormsMove(json.dumps(
        {'form_key': form_key, 'text': answer_text},
        ensure_ascii=False,
    ))


class HtmlFormsPuzzle(Puzzle):
    task_type = 'html_forms'

    parse_move = staticmethod(parse_move)
