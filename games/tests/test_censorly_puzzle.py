from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.censorly import CensorlyMoveRefusal, CensorlyPuzzle


class CensorlyMoveTests(SimpleTestCase):
    def test_json_word_wins_over_guess_and_post(self):
        self.assertEqual(
            CensorlyPuzzle.posted_guess({'word': 'кот', 'guess': 'пёс'}, {'word': 'мышь'}),
            'кот',
        )

    def test_guess_field_is_used_when_word_is_missing(self):
        self.assertEqual(CensorlyPuzzle.posted_guess({'guess': 'пёс'}, {}), 'пёс')

    def test_post_word_is_the_last_resort(self):
        self.assertEqual(CensorlyPuzzle.posted_guess({}, {'word': 'мышь'}), 'мышь')

    def test_missing_hint_token_is_refused_before_any_play_call(self):
        self.assertEqual(
            CensorlyPuzzle.posted_hint_token({'token_id': 'nope'}),
            CensorlyMoveRefusal('Укажите token_id'),
        )
        self.assertEqual(CensorlyPuzzle.posted_hint_token({'token_id': '4'}), 4)

    def test_censorly_is_not_a_send_attempt_hook(self):
        self.assertIsNone(get_puzzle('censorly'))
