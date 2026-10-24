from django.test import SimpleTestCase

from games.puzzles import get_puzzle
from games.puzzles.alphabetty import AlphabettyMove, AlphabettyMoveRefusal, AlphabettyPuzzle


class AlphabettyMoveTests(SimpleTestCase):
    def test_word_wins_over_text(self):
        move = AlphabettyPuzzle.parse_move({'word': ' ёж ', 'text': 'other'})

        self.assertIsInstance(move, AlphabettyMove)
        self.assertEqual(move.word, 'ёж')

    def test_text_is_used_when_word_is_missing(self):
        move = AlphabettyPuzzle.parse_move({'text': ' буква '})

        self.assertEqual(move.word, 'буква')

    def test_blank_word_is_empty_even_when_text_is_set(self):
        self.assertEqual(
            AlphabettyPuzzle.parse_move({'word': '   ', 'text': 'буква'}),
            AlphabettyMoveRefusal('empty'),
        )

    def test_parse_is_not_a_check_attempt_hook(self):
        self.assertIsNone(get_puzzle('alphabetty'))
