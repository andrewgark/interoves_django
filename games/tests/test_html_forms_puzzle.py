import json
from types import SimpleNamespace

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle, parse_posted_text
from games.puzzles.core.move import MoveRefusal, TextMove
from games.puzzles.core.quota import revision_quota_message
from games.puzzles.html_forms import HtmlFormsPuzzle


def _post(**values):
    post = QueryDict(mutable=True)
    for key, value in values.items():
        post[key] = value
    return post


class HtmlFormsMoveTests(SimpleTestCase):
    def test_answer_keeps_cyrillic(self):
        move = HtmlFormsPuzzle.parse_move(_post(form_key='  name ', text='  кот '))

        self.assertIsInstance(move, TextMove)
        self.assertEqual(parse_posted_text('html_forms', _post(form_key='  name ', text='  кот ')), move)
        self.assertEqual(move.text, '{"form_key": "name", "text": "кот"}')
        self.assertEqual(json.loads(move.text), {'form_key': 'name', 'text': 'кот'})

    def test_missing_key_is_invalid_and_blank_text_is_empty(self):
        self.assertEqual(
            HtmlFormsPuzzle.parse_move(_post(form_key='  ', text='кот')),
            MoveRefusal('invalid_form'),
        )
        self.assertEqual(
            HtmlFormsPuzzle.parse_move(_post(form_key='name', text='  ')),
            MoveRefusal('empty'),
        )

    def test_limit_message_counts_the_revision(self):
        task = SimpleNamespace(get_max_attempts=lambda: 1)
        attempt = SimpleNamespace()
        previous = [SimpleNamespace()]

        self.assertIsNone(HtmlFormsPuzzle.attempt_limit_message(attempt, [], task, 'Red'))
        message = HtmlFormsPuzzle.attempt_limit_message(attempt, previous, task, 'Red')
        self.assertEqual(
            message,
            'Team Red exceeds attempts limit (1) in task {}'.format(task),
        )
        self.assertEqual(message, revision_quota_message(attempt, previous, task, 'Red'))

    def test_same_text_names_the_previous_attempt(self):
        other = SimpleNamespace(id=3)
        verdict = HtmlFormsPuzzle.blocks_duplicate(
            'a', 'a', task=None, state_raw=None, other_attempt=other,
        )

        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.attempt_id, 3)

    def test_quota_is_a_check_attempt_hook_without_a_checker(self):
        self.assertIs(get_puzzle('html_forms'), HtmlFormsPuzzle)
        self.assertEqual(HtmlFormsPuzzle.checker_id, 'html_forms')
