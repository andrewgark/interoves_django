import json

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.html_forms import HtmlFormsMove, HtmlFormsMoveRefusal, HtmlFormsPuzzle


def _post(**values):
    post = QueryDict(mutable=True)
    for key, value in values.items():
        post[key] = value
    return post


class HtmlFormsMoveTests(SimpleTestCase):
    def test_answer_keeps_cyrillic(self):
        move = HtmlFormsPuzzle.parse_move(_post(form_key='  name ', text='  кот '))

        self.assertIsInstance(move, HtmlFormsMove)
        self.assertEqual(move.text, '{"form_key": "name", "text": "кот"}')
        self.assertEqual(json.loads(move.text), {'form_key': 'name', 'text': 'кот'})

    def test_missing_key_is_invalid_and_blank_text_is_empty(self):
        self.assertEqual(
            HtmlFormsPuzzle.parse_move(_post(form_key='  ', text='кот')),
            HtmlFormsMoveRefusal('invalid_form'),
        )
        self.assertEqual(
            HtmlFormsPuzzle.parse_move(_post(form_key='name', text='  ')),
            HtmlFormsMoveRefusal('empty'),
        )

    def test_parse_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('html_forms'))
