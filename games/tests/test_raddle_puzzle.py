import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.raddle.submission import RaddleMove, RaddleMoveRefusal


MINI_LADDER = {
    'lengths': [3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD'],
}


def _task():
    return SimpleNamespace(
        id=4,
        task_type='raddle',
        checker_data=json.dumps(MINI_LADDER),
        answer='AAA\nBBB\nCCC\nDDD',
        get_max_attempts=lambda: 2,
    )


class RaddleMoveTests(SimpleTestCase):
    def test_parse_move_keeps_the_posted_word(self):
        move = RaddlePuzzle.parse_move({'word_index': '1', 'word': ' BBB '})

        self.assertIsInstance(move, RaddleMove)
        self.assertEqual(move.word_index, 1)
        self.assertEqual(move.word, 'BBB')
        self.assertIn('"word": "BBB"', move.text)

    def test_blank_word_is_empty_and_garbage_index_is_invalid(self):
        self.assertEqual(
            RaddlePuzzle.parse_move({'word_index': '1', 'word': '  '}),
            RaddleMoveRefusal('empty'),
        )
        self.assertEqual(
            RaddlePuzzle.parse_move({'word_index': 'nope', 'word': 'BBB'}),
            RaddleMoveRefusal('invalid_form'),
        )

    def test_limit_message_counts_only_the_same_word(self):
        task = _task()
        attempt = SimpleNamespace(text=json.dumps({'word_index': 1, 'word': 'ZZZ'}))
        previous = [
            SimpleNamespace(text=json.dumps({'word_index': 1, 'word': 'XXX'})),
            SimpleNamespace(text=json.dumps({'word_index': 2, 'word': 'YYY'})),
        ]

        self.assertIsNone(RaddlePuzzle.attempt_limit_message(attempt, previous, task, None))
        previous.append(SimpleNamespace(text=json.dumps({'word_index': 1, 'word': 'QQQ'})))
        self.assertIn(
            'for word 2',
            RaddlePuzzle.attempt_limit_message(attempt, previous, task, None),
        )

    def test_correct_word_sets_the_response_contract(self):
        attempt = SimpleNamespace(state=json.dumps({'solved_indices': [1]}))
        result = {}

        RaddlePuzzle.write_move_result(result, _task(), attempt, 1, 'BBB')

        self.assertTrue(result['raddle_correct'])
        self.assertEqual(result['raddle_word_index'], 1)
        self.assertNotIn('raddle_needs_sync', result)
        self.assertTrue(RaddlePuzzle.include_task_html(result))

    def test_already_solved_word_asks_the_client_to_sync(self):
        attempt = SimpleNamespace(state=json.dumps({'solved_indices': [1]}))
        result = {}

        RaddlePuzzle.write_move_result(result, _task(), attempt, 1, 'ZZZ')

        self.assertFalse(result['raddle_correct'])
        self.assertTrue(result['raddle_needs_sync'])


class RaddleAttemptAdvancedTests(SimpleTestCase):
    def test_a_new_solved_index_advances_and_a_repeat_or_bad_json_does_not(self):
        grew = SimpleNamespace(state=json.dumps({'solved_indices': [0, 1]}))
        same = SimpleNamespace(state=json.dumps({'solved_indices': [0]}))
        broken = SimpleNamespace(state='{')

        self.assertTrue(RaddlePuzzle.attempt_advanced(grew, json.dumps({'solved_indices': [0]})))
        self.assertFalse(RaddlePuzzle.attempt_advanced(same, json.dumps({'solved_indices': [0]})))
        self.assertFalse(RaddlePuzzle.attempt_advanced(broken, '{'))


class RaddlePlayerHintTests(SimpleTestCase):
    def test_in_game_clue_is_hidden_and_a_normal_hint_stays(self):
        from games.models import Task
        clue = SimpleNamespace(desc='raddle_clue:1')
        shown = SimpleNamespace(desc='Обычная подсказка')
        task = SimpleNamespace(task_type='raddle', get_hints=lambda: [clue, shown])

        self.assertEqual(Task.get_player_hints(task), [shown])


class RaddleHintPenaltyTests(SimpleTestCase):
    def test_in_game_rows_are_skipped_and_a_normal_hint_counts(self):
        self.assertTrue(RaddlePuzzle.skips_hint_penalty(SimpleNamespace(desc='raddle_clue:1')))
        self.assertTrue(RaddlePuzzle.skips_hint_penalty(SimpleNamespace(desc='raddle_answer:2')))
        self.assertFalse(RaddlePuzzle.skips_hint_penalty(SimpleNamespace(desc='Обычная подсказка')))


class RaddleMergeStateTests(SimpleTestCase):
    def test_disjoint_solved_indices_are_united(self):
        task = SimpleNamespace(checker_data=json.dumps(MINI_LADDER), task_type='raddle')
        merged = RaddlePuzzle.merge_state(
            task,
            json.dumps({'solved_indices': [1]}),
            json.dumps({'solved_indices': [2]}),
        )
        self.assertEqual(json.loads(merged)['solved_indices'], [0, 1, 2, 3])
