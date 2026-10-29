"""Лесенка: pure rules of one submitted word (games/puzzles/raddle/transition.py)."""
from decimal import Decimal

from django.test import SimpleTestCase

from games.puzzles.raddle.engine import default_raddle_state, parse_length_mask
from games.puzzles.raddle.transition import advance, progress_status, refuse_word, score_word

# AAA → BBB → CCC → DDD → EEE; the edges start solved.
PARSED = {
    'n_words': 5,
    'hints': ['h0', 'h1', 'h2', 'h3'],
    'masks': [parse_length_mask(3)] * 5,
    'assist': {'enabled': True, 'fractions': [1, 0.5, 0]},
}


def state(solved=(0, 4), **extra):
    st = default_raddle_state(5)
    st['solved_indices'] = sorted(solved)
    st.update(extra)
    return st


class RefuseWordTests(SimpleTestCase):
    def test_playable_word_of_the_right_length_is_checked(self):
        self.assertIsNone(refuse_word(state(), PARSED, 1, 'BBB'))
        self.assertIsNone(refuse_word(state(), PARSED, 3, 'XYZ'))

    def test_refusals_in_the_order_the_server_reports_them(self):
        self.assertEqual(refuse_word(state(), PARSED, 9, ''), 'Пустой ответ')
        self.assertEqual(refuse_word(state(), PARSED, 5, 'BBB'), 'Неверный индекс слова')
        self.assertEqual(refuse_word(state(), PARSED, -1, 'BBB'), 'Неверный индекс слова')
        self.assertEqual(refuse_word(state(), PARSED, 0, 'AAA'), 'Это слово уже решено')
        self.assertEqual(
            refuse_word(state(), PARSED, 2, 'CCC'),
            'Сейчас можно сдавать только крайние нерешённые слова',
        )
        self.assertEqual(refuse_word(state(), PARSED, 1, 'BB'), 'Длина слова не совпадает с маской')


class AdvanceTests(SimpleTestCase):
    def test_word_from_the_top_uses_the_clue_above(self):
        after = advance(state(), PARSED, 1)
        self.assertEqual(after['solved_indices'], [0, 1, 4])
        self.assertEqual(after['used_hints'], [0])

    def test_word_from_the_bottom_uses_the_clue_below(self):
        after = advance(state(), PARSED, 3)
        self.assertEqual(after['used_hints'], [3])

    def test_last_middle_word_uses_both_clues(self):
        after = advance(state(solved=(0, 1, 3, 4), used_hints=[0, 3]), PARSED, 2)
        self.assertEqual(after['solved_indices'], [0, 1, 2, 3, 4])
        self.assertEqual(after['used_hints'], [0, 1, 2, 3])

    def test_other_keys_survive_and_input_is_not_mutated(self):
        before = state(drafts={'2': 'CC'}, total=1.0)
        after = advance(before, PARSED, 1)
        self.assertEqual(before['solved_indices'], [0, 4])
        self.assertEqual(after['drafts'], {'2': 'CC'})
        self.assertEqual(after['total'], 1.0)


class ProgressStatusTests(SimpleTestCase):
    def test_no_middle_word_yet(self):
        self.assertEqual(progress_status(state(), 5, word_correct=False), ('Wrong', 'Pending'))

    def test_some_middle_words(self):
        st = state(solved=(0, 1, 4))
        self.assertEqual(progress_status(st, 5, word_correct=True), ('Partial', 'Partial'))
        self.assertEqual(progress_status(st, 5, word_correct=False), ('Partial', 'Pending'))

    def test_all_middle_words(self):
        st = state(solved=(0, 1, 2, 3, 4))
        self.assertEqual(progress_status(st, 5, word_correct=False), ('Ok', 'Ok'))

    def test_two_word_ladder_has_nothing_to_solve(self):
        self.assertEqual(progress_status({'solved_indices': [0, 1]}, 2, word_correct=False), ('Wrong', 'Pending'))


class ScoreWordTests(SimpleTestCase):
    def test_full_credit_without_assist(self):
        self.assertEqual(score_word(state(), PARSED, 1), Decimal('1'))

    def test_credit_reduced_by_assist_tier(self):
        self.assertEqual(score_word(state(assist_tier={'1': 1}), PARSED, 1), Decimal('0.5'))
        self.assertEqual(score_word(state(assist_tier={'1': 2}), PARSED, 1), Decimal('0'))

    def test_edges_give_no_points(self):
        self.assertEqual(score_word(state(), PARSED, 0), Decimal('0'))
        self.assertEqual(score_word(state(), PARSED, 4), Decimal('0'))
