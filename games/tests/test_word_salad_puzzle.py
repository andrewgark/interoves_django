import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from games.puzzles.word_salad import WordSaladPuzzle
from games.puzzles.word_salad_submission import (
    SaladMove,
    SaladMoveRefusal,
    SaladSync,
)
from games.word_salad import RARE_FOUND_COMMENT


class WordSaladMoveTests(SimpleTestCase):
    def test_solve_path_starts_a_game_and_keeps_the_posted_action(self):
        move = WordSaladPuzzle.parse_move({'path': '[1, 2]'})

        self.assertIsInstance(move, SaladMove)
        self.assertEqual(move.action, 'solve')
        self.assertTrue(move.starts_game)
        self.assertEqual(json.loads(move.text)['path'], [1, 2])

    def test_unknown_action_still_submits_a_solve_path(self):
        move = WordSaladPuzzle.parse_move({'action': 'foo', 'path': '[3]'})

        self.assertEqual(move.action, 'foo')
        self.assertEqual(json.loads(move.text)['action'], 'solve')

    def test_empty_path_and_bad_hint_index_refuse(self):
        self.assertEqual(WordSaladPuzzle.parse_move({'path': '[]'}), SaladMoveRefusal('empty'))
        self.assertEqual(
            WordSaladPuzzle.parse_move({'action': 'hint', 'word_index': '-1'}),
            SaladMoveRefusal('empty'),
        )

    def test_sync_finds_is_its_own_request(self):
        self.assertIsInstance(WordSaladPuzzle.parse_move({'action': 'sync_finds'}), SaladSync)

    def test_same_text_names_the_previous_attempt(self):
        other = SimpleNamespace(id=9)

        self.assertFalse(WordSaladPuzzle.blocks_duplicate('a', 'b', task=None, state_raw=None, other_attempt=other).blocked)
        verdict = WordSaladPuzzle.blocks_duplicate('a', 'a', task=None, state_raw=None, other_attempt=other)
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.attempt_id, 9)

    def test_credited_solve_is_correct_and_clears_the_selection(self):
        result = {}
        attempt = SimpleNamespace(comment='')

        WordSaladPuzzle.write_move_result(
            result, None, attempt, attempt_persisted=True, user=None, anon_key=None,
        )

        self.assertTrue(result['word_salad_correct'])
        self.assertFalse(result['word_salad_keep_selection'])
        self.assertTrue(WordSaladPuzzle.include_task_html(result))

    @patch(
        'games.puzzles.word_salad_submission.path_word_from_attempt',
        return_value=('РЕКА', [], []),
    )
    def test_rare_find_keeps_the_selection(self, _path_word):
        result = {}
        attempt = SimpleNamespace(comment=RARE_FOUND_COMMENT)

        WordSaladPuzzle.write_move_result(
            result, None, attempt, attempt_persisted=True, user=None, anon_key=None,
        )

        self.assertFalse(result['word_salad_correct'])
        self.assertEqual(result['word_salad_rare'], 'РЕКА')
        self.assertTrue(result['word_salad_keep_selection'])
        self.assertFalse(WordSaladPuzzle.include_task_html(result))
