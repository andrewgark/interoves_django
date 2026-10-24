import json
from types import SimpleNamespace

from django.http import QueryDict
from django.test import SimpleTestCase

from games.puzzles import get_puzzle, parse_posted_text
from games.puzzles.move import MoveRefusal, TextMove
from games.puzzles.wall import WallPuzzle


def _post(**values):
    post = QueryDict(mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            post.setlist(key, value)
        else:
            post[key] = value
    return post


class WallMoveTests(SimpleTestCase):
    def test_word_group_is_sorted_when_posted_as_a_list(self):
        move = WallPuzzle.parse_move(_post(stage='cat_words', **{'words[]': ['б', 'а']}))

        self.assertIsInstance(move, TextMove)
        self.assertEqual(parse_posted_text('wall', _post(stage='cat_words', **{'words[]': ['б', 'а']})), move)
        self.assertEqual(json.loads(move.text), {'words': ['а', 'б'], 'stage': 'cat_words'})

    def test_explanation_keeps_posted_order_and_strips_text(self):
        move = WallPuzzle.parse_move(_post(
            stage='cat_explanation', words='["б", "а"]', text='  почему ',
        ))

        self.assertEqual(
            json.loads(move.text),
            {'explanation': 'почему', 'words': ['б', 'а'], 'stage': 'cat_explanation'},
        )

    def test_blank_explanation_is_empty_and_bad_json_is_invalid(self):
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='cat_explanation', words='["а"]', text='  ')),
            MoveRefusal('empty'),
        )
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='cat_words', words='{', text='x')),
            MoveRefusal('invalid_form'),
        )
        self.assertEqual(
            WallPuzzle.parse_move(_post(stage='other')),
            MoveRefusal('invalid_form'),
        )

    def test_quota_uses_every_attempt_and_the_raw_chain_state(self):
        attempts = ['all']
        chain_state = '{"guessed_words": []}'
        seen = {}

        def validate_max_attempts(got_attempts, attempt, current_state=None):
            seen['attempts'] = got_attempts
            seen['state'] = current_state
            if got_attempts is attempts:
                return 'cat_words', 3, 3
            return None

        task = SimpleNamespace(get_wall=lambda: SimpleNamespace(validate_max_attempts=validate_max_attempts))
        attempt = SimpleNamespace(text='{}')
        message = WallPuzzle.attempt_limit_message(
            attempt, ['revision-only'], task, 'Red',
            attempts=attempts, chain_state=chain_state,
        )

        self.assertEqual(seen['attempts'], attempts)
        self.assertEqual(seen['state'], chain_state)
        self.assertEqual(
            message,
            'Team Red exceeds attempts limit (3) in wall task {} on stage cat_words'.format(task),
        )
        self.assertIsNone(WallPuzzle.attempt_limit_message(
            attempt, [], task, 'Red', attempts=[], chain_state=None,
        ))

    def test_same_text_names_the_previous_attempt(self):
        other = SimpleNamespace(id=4)
        verdict = WallPuzzle.blocks_duplicate('a', 'a', task=None, state_raw=None, other_attempt=other)

        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.attempt_id, 4)

    def test_quota_is_a_check_attempt_hook_without_a_checker(self):
        self.assertIs(get_puzzle('wall'), WallPuzzle)
        self.assertIsNone(WallPuzzle.checker_id)
        self.assertIsNone(parse_posted_text('raddle', _post(stage='cat_words')))
