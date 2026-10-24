import json

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle, parse_posted_text
from games.puzzles.move import MoveRefusal, TextMove
from games.puzzles.wall import WallPuzzle


def _post(**values):
    post = QueryDict(mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            post.setlist(key, value)
        else:
            post[key] = value
    return post


class WallMoveTests(SimpleTestCase):
    def test_word_group_is_sorted_when_posted_as_a_list(self):
        move = WallPuzzle.parse_move(_post(stage='cat_words', **{'words[]': ['б', 'а']}))

        self.assertIsInstance(move, TextMove)
        self.assertEqual(parse_posted_text('wall', _post(stage='cat_words', **{'words[]': ['б', 'а']})), move)
        self.assertEqual(json.loads(move.text), {'words': ['а', 'б'], 'stage': 'cat_words'})

    def test_explanation_keeps_posted_order_and_strips_text(self):
        move = WallPuzzle.parse_move(_post(
            stage='cat_explanation', words='["б", "а"]', text='  почему ',
        ))

        self.assertEqual(
            json.loads(move.text),
            {'explanation': 'почему', 'words': ['б', 'а'], 'stage': 'cat_explanation'},
        )

    def test_blank_explanation_is_empty_and_bad_json_is_invalid(self):
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='cat_explanation', words='["а"]', text='  ')),
            MoveRefusal('empty'),
        )
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='cat_words', words='{', text='x')),
            MoveRefusal('invalid_form'),
        )
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='other')),
            MoveRefusal('invalid_form'),
        )

    def test_parse_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('wall'))
        self.assertIsNone(parse_posted_text('raddle', _post(stage='cat_words')))
