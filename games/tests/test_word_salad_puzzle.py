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


class WordSaladAttemptAdvancedTests(SimpleTestCase):
    def test_a_new_solved_index_advances_and_a_repeat_or_bad_json_does_not(self):
        grew = SimpleNamespace(state=json.dumps({'solved_indices': [0, 1]}))
        same = SimpleNamespace(state=json.dumps({'solved_indices': [0]}))
        broken = SimpleNamespace(state='{')

        self.assertTrue(WordSaladPuzzle.attempt_advanced(grew, json.dumps({'solved_indices': [0]})))
        self.assertFalse(WordSaladPuzzle.attempt_advanced(same, json.dumps({'solved_indices': [0]})))
        self.assertFalse(WordSaladPuzzle.attempt_advanced(broken, '{'))


class WordSaladResultAttemptTests(SimpleTestCase):
    def test_the_row_shows_the_latest_attempt_when_an_earlier_one_scored_more(self):
        from games.models import AttemptsInfo
        task = SimpleNamespace(task_type='word_salad')
        latest = SimpleNamespace(task=task, id='latest')
        earlier = SimpleNamespace(task=task, id='earlier')
        info = SimpleNamespace(last_attempt=latest, best_attempt=earlier)

        self.assertIs(AttemptsInfo.get_result_attempt(info), latest)

    def test_another_puzzle_shows_the_best_attempt(self):
        from games.models import AttemptsInfo
        task = SimpleNamespace(task_type='default')
        latest = SimpleNamespace(task=task, id='latest')
        earlier = SimpleNamespace(task=task, id='earlier')
        info = SimpleNamespace(last_attempt=latest, best_attempt=earlier)

        self.assertIs(AttemptsInfo.get_result_attempt(info), earlier)


class WordSaladMergeStateTests(SimpleTestCase):
    def test_disjoint_solved_indices_are_united(self):
        task = SimpleNamespace(
            task_type='word_salad',
            checker_data=json.dumps({
                'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
                'words': ['ABCDEFGHIJKLMNOP'],
            }),
            answer='',
        )
        merged = WordSaladPuzzle.merge_state(
            task,
            json.dumps({'solved_indices': [0]}),
            json.dumps({'solved_indices': [1]}),
        )
        self.assertEqual(json.loads(merged)['solved_indices'], [0, 1])
