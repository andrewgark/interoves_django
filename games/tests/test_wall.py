import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from games.wall import Wall, get_wall_default_max_attempts


def _wall_task(*, n_cat=4, attempts=None, max_attempts=3):
    words = [f'w{i}' for i in range(n_cat * 4)]
    text = json.dumps({
        'n_cat': n_cat,
        'n_words': 4,
        'words': words,
        **({'attempts': attempts} if attempts is not None else {}),
    })
    checker_data = json.dumps({
        'points_words': 1,
        'points_explanation': 1,
        'points_bonus': 1,
        'answers': [
            {'words': words[i * 4:(i + 1) * 4], 'explanation': f'cat{i}'}
            for i in range(n_cat)
        ],
    })
    task = MagicMock()
    task.text = text
    task.checker_data = checker_data
    task.get_max_attempts.return_value = max_attempts
    return task


def _attempts_info(guessed_words, *, n_wrong_word_attempts=0):
    """Build a minimal attempts_info with exhausted cat_words attempts if requested."""
    attempts = []
    for _ in range(n_wrong_word_attempts):
        state = {
            'guessed_words': list(guessed_words),
            'last_attempt': {
                'stage': 'cat_words',
                'status': 'Wrong',
                'words': ['x', 'y', 'z', 'w'],
            },
        }
        attempts.append(SimpleNamespace(state=json.dumps(state)))
    last_state = {
        'guessed_words': list(guessed_words),
        'last_attempt': {
            'stage': 'cat_words',
            'status': 'Wrong',
            'words': ['x', 'y', 'z', 'w'],
        },
    }
    last = attempts[-1] if attempts else SimpleNamespace(state=json.dumps(last_state))
    if not attempts:
        attempts = [last]
    return SimpleNamespace(last_attempt=last, attempts=attempts)


class WallDefaultMaxAttemptsTests(SimpleTestCase):
    def test_four_categories_is_5_4_3(self):
        self.assertEqual(get_wall_default_max_attempts(4), [5, 4, 3])

    def test_scales_with_n_cat(self):
        self.assertEqual(get_wall_default_max_attempts(3), [4, 3])
        self.assertEqual(get_wall_default_max_attempts(5), [6, 5, 4, 3])


class WallGuessingTilesIsOverTests(SimpleTestCase):
    def test_tournament_stops_when_word_attempts_exhausted(self):
        wall = Wall(_wall_task(attempts=[1, 1, 1]))
        info = _attempts_info([], n_wrong_word_attempts=1)
        self.assertTrue(wall.guessing_tiles_is_over(info, mode='tournament'))

    def test_general_allows_more_word_attempts(self):
        wall = Wall(_wall_task(attempts=[1, 1, 1]))
        info = _attempts_info([], n_wrong_word_attempts=1)
        self.assertFalse(wall.guessing_tiles_is_over(info, mode='general'))

    def test_stops_when_all_categories_guessed_in_any_mode(self):
        wall = Wall(_wall_task())
        cats = [
            ['A', 'B', 'C', 'D'],
            ['E', 'F', 'G', 'H'],
            ['I', 'J', 'K', 'L'],
            ['M', 'N', 'O', 'P'],
        ]
        info = _attempts_info(cats, n_wrong_word_attempts=0)
        # Rewrite last attempt as Ok with all categories present.
        info.last_attempt.state = json.dumps({
            'guessed_words': cats,
            'last_attempt': {'stage': 'cat_words', 'status': 'Ok', 'words': cats[-1]},
        })
        info.attempts = [info.last_attempt]
        self.assertTrue(wall.guessing_tiles_is_over(info, mode='general'))
        self.assertTrue(wall.guessing_tiles_is_over(info, mode='tournament'))
