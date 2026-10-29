"""games.gameplay.moves.submit_move without an HTTP request, and read_move."""
import json
import uuid
from unittest.mock import patch

from django.http import QueryDict

from games.gameplay.moves import MoveContext, MoveInput, submit_move
from games.models import Attempt, CheckerType, ClientEventReceipt, Task
from games.puzzles.core.primitives import MoveRefusal
from games.site.http.exceptions import DuplicateAttemptException, InvalidFormException
from games.site.views.game.attempt import read_move
from games.tests.test_client_event_receipts import _ClientEventFixture
from games.tests.test_raddle_send_attempt import LONG_LADDER


def post(**data):
    q = QueryDict(mutable=True)
    q.update(data)
    return q


class ReadMoveTests(_ClientEventFixture):
    def test_ordinary_answer_is_stripped(self):
        move = read_move(self.plain, post(text='  ответ  '))
        self.assertEqual(move, MoveInput(text='ответ'))

    def test_empty_ordinary_answer_is_invalid(self):
        with self.assertRaises(InvalidFormException):
            read_move(self.plain, post(text='   '))

    def test_ladder_word(self):
        event_id = uuid.uuid4()
        move = read_move(self.ladder, post(word_index='1', word='BBB', client_event_id=str(event_id)))
        self.assertEqual(move.word_index, 1)
        self.assertEqual(move.word, 'BBB')
        self.assertEqual(json.loads(move.text), {'word_index': 1, 'word': 'BBB'})
        self.assertEqual(move.client_event_id, event_id)
        self.assertTrue(move.persist_wrong)

    def test_ladder_refusal_keeps_its_status(self):
        self.assertEqual(read_move(self.ladder, post(word_index='1', word='')), MoveRefusal('empty'))


class SubmitMoveTests(_ClientEventFixture):
    def _ctx(self):
        return MoveContext(task=self.ladder, game=self.game, anon_key=self.anon_key)

    def _word(self, word_index, word, event_id=None):
        return MoveInput(
            text=json.dumps({'word_index': word_index, 'word': word}, ensure_ascii=False),
            word_index=word_index,
            word=word,
            client_event_id=event_id,
        )

    def test_correct_word_is_checked_and_persisted(self):
        outcome = submit_move(self._ctx(), self._word(1, 'BBB'))
        self.assertEqual(outcome.kind, 'checked')
        self.assertTrue(outcome.persisted)
        self.assertEqual(outcome.ctx.mode, 'general')
        self.assertIn(1, json.loads(outcome.attempt.state)['solved_indices'])
        self.assertEqual(outcome.attempt.anon_key, self.anon_key)

    def test_middle_word_that_is_not_playable_yet_is_stale_and_not_saved(self):
        with patch('games.realtime.track_compat.track_task_change'):
            long_ladder = Task.objects.create(
                task_group=self.tg, number='3', task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'), points=1,
                checker_data=json.dumps(LONG_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
            )
        ctx = MoveContext(task=long_ladder, game=self.game, anon_key=self.anon_key)
        outcome = submit_move(ctx, self._word(2, 'CCC'))
        self.assertEqual(outcome.kind, 'stale_word')
        self.assertTrue(outcome.stale['raddle_stale_ui'])
        self.assertFalse(Attempt.manager.filter(task=long_ladder).exists())

    def test_same_word_sent_again_is_a_duplicate(self):
        submit_move(self._ctx(), self._word(1, 'BBB'))
        with self.assertRaises(DuplicateAttemptException):
            submit_move(self._ctx(), self._word(1, 'BBB'))

    def test_resent_event_is_replayed(self):
        event_id = uuid.uuid4()
        first = submit_move(self._ctx(), self._word(1, 'BBB', event_id))
        again = submit_move(self._ctx(), self._word(1, 'BBB', event_id))
        self.assertEqual(again.kind, 'replayed')
        self.assertEqual(again.attempt.pk, first.attempt.pk)
        self.assertEqual(ClientEventReceipt.objects.count(), 1)
