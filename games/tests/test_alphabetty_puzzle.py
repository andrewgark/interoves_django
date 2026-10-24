from types import SimpleNamespace
from unittest.mock import patch

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

    def test_daily_word_comes_from_json_then_the_form(self):
        self.assertEqual(AlphabettyPuzzle.posted_guess({'word': 'кот'}, {'word': 'пёс'}), 'кот')
        self.assertEqual(AlphabettyPuzzle.posted_guess({}, {'word': 'пёс'}), 'пёс')
        self.assertEqual(AlphabettyPuzzle.posted_guess({'word': ''}, {'word': 'пёс'}), 'пёс')

    @patch('games.puzzles.alphabetty.apply_guess', return_value={'status': 'correct'})
    def test_posted_guess_is_applied_without_rewriting_the_word(self, apply_guess):
        result = AlphabettyPuzzle.apply_posted_guess(
            {'word': ' Кот '}, {}, game='g', task='t',
        )

        self.assertEqual(result['status'], 'correct')
        apply_guess.assert_called_once_with(word=' Кот ', game='g', task='t')

    @patch('games.puzzles.alphabetty.apply_hint', return_value={'status': 'ok'})
    def test_posted_hint_is_applied_with_the_play_arguments(self, apply_hint):
        result = AlphabettyPuzzle.apply_posted_hint(game='g', replay_slot=None)

        self.assertEqual(result['status'], 'ok')
        apply_hint.assert_called_once_with(game='g', replay_slot=None)


class AlphabettyAttemptAdvancedTests(SimpleTestCase):
    def test_only_an_accepted_answer_advances(self):
        self.assertTrue(AlphabettyPuzzle.attempt_advanced(SimpleNamespace(status='Ok'), None))
        self.assertFalse(AlphabettyPuzzle.attempt_advanced(SimpleNamespace(status='Partial'), None))
        self.assertIsNone(get_puzzle('alphabetty'))


class AlphabettyLetterHintTests(SimpleTestCase):
    def test_a_precomputed_penalty_is_kept(self):
        info = SimpleNamespace(letter_hint_penalty=4, attempts=[], best_attempt=None)
        self.assertEqual(AlphabettyPuzzle.letter_hint_penalty(info), 4)

    def test_another_puzzle_adds_nothing(self):
        sample = SimpleNamespace(task=SimpleNamespace(task_type='default'))
        info = SimpleNamespace(letter_hint_penalty=None, attempts=[sample], best_attempt=None)
        self.assertEqual(AlphabettyPuzzle.letter_hint_penalty(info), 0)
