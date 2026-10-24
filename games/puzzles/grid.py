"""Сетка: one posted walls or shading payload. The view still saves the attempt."""

from __future__ import annotations

import json

from games.grid_puzzle import (
    GridPuzzleDataError,
    grid_checker_id,
    parse_grid_puzzle_attempt,
    parse_grid_puzzle_data,
    parse_grid_shading_attempt,
    validate_grid_checker_data,
)
from games.puzzles.base import Puzzle
from games.puzzles.move import MoveRefusal, TextMove


def parse_move(post, task):
    """Read a grid POST. A bad payload is the existing invalid_form status."""
    try:
        puzzle = parse_grid_puzzle_data(task.checker_data)
        checker_id = grid_checker_id(task)
        validate_grid_checker_data(puzzle, checker_id)
        if checker_id == 'grid-wall-checker':
            walls_raw = post.get('walls', '[]')
            attempt_payload = parse_grid_puzzle_attempt(
                json.dumps({'walls': json.loads(walls_raw)}),
                puzzle['rows'],
                puzzle['cols'],
                can_set_walls=puzzle['can_set_walls'],
            )
        else:
            shading_raw = post.get('shading', '[]')
            attempt_payload = parse_grid_shading_attempt(
                json.dumps({'shading': json.loads(shading_raw)}),
                puzzle['rows'],
                puzzle['cols'],
            )
    except (GridPuzzleDataError, TypeError, ValueError):
        return MoveRefusal('invalid_form')
    return TextMove(json.dumps(attempt_payload, separators=(',', ':')))


def clean_checker_data(task):
    """Reject a grid payload the checker would refuse."""
    from django.core.exceptions import ValidationError
    try:
        parsed = parse_grid_puzzle_data(task.checker_data)
        validate_grid_checker_data(parsed, grid_checker_id(task))
    except GridPuzzleDataError as exc:
        raise ValidationError({'checker_data': str(exc)})


def pretty_text(attempt):
    """List label for a grid walls or shading attempt."""
    try:
        payload = json.loads(attempt.text)
        if 'shading' in payload:
            joined = ''.join(payload.get('shading') or [])
            return '{} black, {} green cells'.format(joined.count('B'), joined.count('G'))
        walls = payload.get('walls', [])
        return '{} {}'.format(len(walls), 'wall' if len(walls) == 1 else 'walls')
    except (TypeError, ValueError, AttributeError):
        return attempt.text


def read_post(post, task):
    """Read a grid POST. The checker payload still comes from the task."""
    return parse_move(post, task)


def write_response(response):
    """The grid flag is true only when the checker accepted the picture."""
    response.result['grid_puzzle_correct'] = response.attempt.status == 'Ok'


class GridPuzzle(Puzzle):
    task_type = 'grid-puzzle'

    parse_move = staticmethod(parse_move)
    read_post = staticmethod(read_post)
    clean_checker_data = staticmethod(clean_checker_data)
    pretty_text = staticmethod(pretty_text)
    write_response = staticmethod(write_response)
