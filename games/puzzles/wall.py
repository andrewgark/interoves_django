"""Стена: one posted group. The view still saves the attempt and checks the quota."""

from __future__ import annotations

import json

from games.puzzles.base import Puzzle
from games.puzzles.move import MoveRefusal, TextMove


def parse_move(post):
    """Read a wall POST. Statuses are the existing JSON codes."""
    stage = post.get('stage')
    if stage not in ('cat_words', 'cat_explanation'):
        return MoveRefusal('invalid_form')
    try:
        if 'text' not in post:
            words = post.getlist('words[]')
            request_data = {'words': sorted(words), 'stage': stage}
        else:
            words = json.loads(post.get('words', ''))
            if not isinstance(words, list):
                raise ValueError('wall words must be a list')
            request_data = {
                'explanation': (post.get('text') or '').strip(),
                'words': words,
                'stage': stage,
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        return MoveRefusal('invalid_form')
    if not request_data['words'] or any(not isinstance(word, str) for word in request_data['words']):
        return MoveRefusal('invalid_form')
    if stage == 'cat_explanation' and not request_data.get('explanation'):
        return MoveRefusal('empty')
    return TextMove(json.dumps(request_data))


class WallPuzzle(Puzzle):
    task_type = 'wall'

    parse_move = staticmethod(parse_move)
