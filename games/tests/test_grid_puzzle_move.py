import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.grid import GridPuzzle
from games.puzzles.move import MoveRefusal, TextMove


WALLS = {
    'version': 1,
    'rows': 3,
    'cols': 4,
    'solution_walls': ['v:0:2', 'h:1:0', 'h:2:3'],
}


def _task(checker_id='grid-wall-checker', puzzle=WALLS):
    return SimpleNamespace(
        checker_data=json.dumps(puzzle),
        checker=SimpleNamespace(id=checker_id),
        task_group=None,
    )


class GridMoveTests(SimpleTestCase):
    def test_walls_are_compact_json(self):
        move = GridPuzzle.parse_move({'walls': '["h:1:0"]'}, _task())

        self.assertIsInstance(move, TextMove)
        self.assertEqual(move.text, '{"walls":["h:1:0"]}')

    def test_bad_json_is_invalid_and_grid_is_not_a_check_hook(self):
        self.assertEqual(
            GridPuzzle.parse_move({'walls': '{'}, _task()),
            MoveRefusal('invalid_form'),
        )
        self.assertIsNone(get_puzzle('grid-puzzle'))
