import json
from types import SimpleNamespace

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle, parse_posted_text
from games.puzzles.core.move import MoveRefusal, TextMove
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

    def test_limit_message_counts_only_the_same_line(self):
        task = SimpleNamespace(get_max_attempts=lambda: 2)
        attempt = SimpleNamespace(text=json.dumps({'line_index': 1, 'answers': ['а']}))
        previous = [
            SimpleNamespace(text=json.dumps({'line_index': 1, 'answers': ['б']})),
            SimpleNamespace(text=json.dumps({'line_index': 0, 'answers': ['в']})),
        ]

        self.assertIsNone(ReplacementsPuzzle.attempt_limit_message(
            attempt, previous, task, None, attempts=['ignored'], chain_state='raw',
        ))
        previous.append(SimpleNamespace(text=json.dumps({'line_index': 1, 'answers': ['г']})))
        self.assertIn(
            'for line 2',
            ReplacementsPuzzle.attempt_limit_message(attempt, previous, task, None),
        )

    def test_same_text_names_the_previous_attempt(self):
        other = SimpleNamespace(id=8)
        verdict = ReplacementsPuzzle.blocks_duplicate(
            'a', 'a', task=None, state_raw=None, other_attempt=other,
        )

        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.attempt_id, 8)

    def test_quota_is_a_check_attempt_hook_without_a_checker(self):
        self.assertIs(get_puzzle('replacements_lines'), ReplacementsPuzzle)
        self.assertEqual(ReplacementsPuzzle.checker_id, 'replacements_lines')
