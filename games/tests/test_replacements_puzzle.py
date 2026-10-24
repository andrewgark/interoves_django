import json

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle, parse_posted_text
from games.puzzles.move import MoveRefusal, TextMove
from games.puzzles.replacements import ReplacementsPuzzle


def _post(**values):
    post = QueryDict(mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            post.setlist(key, value)
        else:
            post[key] = value
    return post


class ReplacementsMoveTests(SimpleTestCase):
    def test_json_answers_keep_the_line(self):
        move = ReplacementsPuzzle.parse_move(_post(line_index='2', answers='["кот", "пёс"]'))

        self.assertIsInstance(move, TextMove)
        self.assertEqual(parse_posted_text('replacements_lines', _post(line_index='2', answers='["кот", "пёс"]')), move)
        self.assertEqual(json.loads(move.text), {'line_index': 2, 'answers': ['кот', 'пёс']})

    def test_invalid_json_falls_back_to_the_list_field(self):
        move = ReplacementsPuzzle.parse_move(_post(answers='not-json', **{'answers[]': ['кот']}))

        self.assertEqual(json.loads(move.text)['answers'], ['кот'])

    def test_blank_answers_are_empty_and_a_bad_line_is_invalid(self):
        self.assertEqual(
            ReplacementsPuzzle.parse_move(_post(answers='["  ", ""]')),
            MoveRefusal('empty'),
        )
        self.assertEqual(
            ReplacementsPuzzle.parse_move(_post(line_index='nope', answers='["кот"]')),
            MoveRefusal('invalid_form'),
        )
        self.assertEqual(
            ReplacementsPuzzle.parse_move(_post(answers='{"a": 1}')),
            MoveRefusal('invalid_form'),
        )

    def test_parse_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('replacements_lines'))
