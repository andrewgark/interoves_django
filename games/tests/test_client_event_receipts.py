"""Idempotent /send_attempt/ by client_event_id: a resent move gets its first verdict."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from games.gameplay import attempts as gameplay_attempts
from games.gameplay import client_events
from games.gameplay import moves as gameplay_moves
from games.models import Attempt, CheckerType, ClientEventReceipt, Game, GameTaskGroup, Task, TaskGroup
from games.ops.analytics.identity import attach_anon_cookie
from games.site.http.exceptions import ReplayedClientEventException
from games.tests.test_raddle_send_attempt import MINI_LADDER, _ensure_fixtures


class _ClientEventFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        CheckerType.objects.get_or_create(pk='equals')
        now = timezone.now()
        with patch('games.realtime.track_compat.track_task_change'):
            cls.game = Game.objects.create(
                id='client_event_test',
                name='Client events',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
                is_tournament=False,
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=1),
            )
            cls.tg = TaskGroup.objects.create(label='client_event_tg')
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='L1')
            cls.ladder = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(MINI_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD',
            )
            cls.plain = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='default',
                checker=CheckerType.objects.get(pk='equals'),
                points=1,
                checker_data='ответ',
                answer='ответ',
            )
        cls.anon_key = 'client-event-anon'

    def setUp(self):
        self.client = Client()
        attach_anon_cookie(self.client, self.anon_key)

    def _post(self, task, data, *, client=None, anon_key=None):
        return (client or self.client).post(
            '/send_attempt/{}/'.format(task.id),
            {'game_id': self.game.id, 'anon_key': anon_key or self.anon_key, **data},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()

    def _post_word(self, word_index, word, event_id, **kwargs):
        data = {'word_index': word_index, 'word': word}
        if event_id is not None:
            data['client_event_id'] = str(event_id)
        return self._post(self.ladder, data, **kwargs)


class ClientEventReplayTests(_ClientEventFixture):
    def test_resent_correct_word_repeats_the_correct_verdict(self):
        event_id = uuid.uuid4()
        first = self._post_word(1, 'BBB', event_id)
        self.assertTrue(first['raddle_correct'])
        self.assertNotIn('client_event_replayed', first)

        # Without the id this resend is a "word is no longer playable" sync.
        second = self._post_word(1, 'BBB', event_id)
        self.assertEqual(second['status'], 'ok')
        self.assertTrue(second['client_event_replayed'])
        self.assertTrue(second['raddle_correct'])
        self.assertEqual(second['raddle_word_index'], 1)
        self.assertIn('update_task_html_new', second)
        self.assertEqual(Attempt.manager.filter(task=self.ladder).count(), 1)
        self.assertEqual(ClientEventReceipt.objects.filter(client_event_id=event_id).count(), 1)

    def test_resent_wrong_word_repeats_the_wrong_verdict(self):
        event_id = uuid.uuid4()
        first = self._post_word(1, 'ZZZ', event_id)
        self.assertFalse(first['raddle_correct'])

        second = self._post_word(1, 'ZZZ', event_id)
        self.assertEqual(second['status'], 'ok')
        self.assertTrue(second['client_event_replayed'])
        self.assertFalse(second['raddle_correct'])
        self.assertNotIn('raddle_needs_sync', second)
        self.assertEqual(Attempt.manager.filter(task=self.ladder).count(), 1)

    def test_new_event_id_with_the_same_word_is_still_a_duplicate(self):
        self._post_word(1, 'ZZZ', uuid.uuid4())
        second = self._post_word(1, 'ZZZ', uuid.uuid4())
        self.assertEqual(second['status'], 'duplicate')

    def test_resent_ordinary_answer_repeats_attempt_status(self):
        event_id = str(uuid.uuid4())
        first = self._post(self.plain, {'text': 'Ответ', 'client_event_id': event_id})
        second = self._post(self.plain, {'text': 'Ответ', 'client_event_id': event_id})
        self.assertEqual(first['attempt_status'], 'Ok')
        self.assertEqual(second['attempt_status'], 'Ok')
        self.assertEqual(second['attempt_id'], first['attempt_id'])
        self.assertTrue(second['client_event_replayed'])
        self.assertEqual(Attempt.manager.filter(task=self.plain).count(), 1)

    def test_event_id_of_another_actor_is_refused(self):
        event_id = uuid.uuid4()
        self._post_word(1, 'BBB', event_id)

        other = Client()
        attach_anon_cookie(other, 'client-event-other')
        data = self._post_word(1, 'BBB', event_id, client=other, anon_key='client-event-other')
        self.assertEqual(data['status'], 'client_event_conflict')
        self.assertFalse(Attempt.manager.filter(anon_key='client-event-other').exists())

    def test_event_id_of_another_task_is_refused(self):
        event_id = str(uuid.uuid4())
        self._post(self.plain, {'text': 'ответ', 'client_event_id': event_id})
        data = self._post_word(1, 'BBB', event_id)
        self.assertEqual(data['status'], 'client_event_conflict')

    def test_malformed_event_id_is_ignored(self):
        data = self._post_word(1, 'BBB', 'not-a-uuid')
        self.assertTrue(data['raddle_correct'])
        self.assertFalse(ClientEventReceipt.objects.exists())

    def test_wrong_move_that_is_not_persisted_writes_no_receipt(self):
        with patch.object(gameplay_moves, 'check_attempt', return_value=False):
            self._post_word(1, 'ZZZ', uuid.uuid4())
        self.assertFalse(ClientEventReceipt.objects.exists())


class CheckAttemptReceiptTests(_ClientEventFixture):
    """The receipt lookup under the lock and the unique-key backstop."""

    def _stored_attempt(self, event_id):
        self._post_word(1, 'ZZZ', event_id)
        return Attempt.manager.get(task=self.ladder)

    def _new_attempt(self):
        return Attempt(
            task=self.ladder, game=self.game, anon_key=self.anon_key,
            text=json.dumps({'word_index': 1, 'word': 'ZZZ'}),
        )

    def test_check_attempt_reports_the_stored_attempt(self):
        event_id = uuid.uuid4()
        stored = self._stored_attempt(event_id)
        with self.assertRaises(ReplayedClientEventException) as ctx:
            gameplay_attempts.check_attempt(self._new_attempt(), client_event_id=event_id)
        self.assertEqual(ctx.exception.attempt_id, stored.pk)

    def test_concurrent_resend_is_resolved_by_the_unique_receipt(self):
        event_id = uuid.uuid4()
        stored = self._stored_attempt(event_id)
        # A stale snapshot misses the receipt under the lock; the insert then
        # collides and the committed receipt answers instead.
        lookups = iter([None])

        def stale_then_real(client_event_id, attempt):
            return next(lookups, None) or client_events.receipt_attempt(client_event_id, attempt)

        attempt = Attempt(
            task=self.ladder, game=self.game, anon_key=self.anon_key,
            text=json.dumps({'word_index': 2, 'word': 'QQQ'}),
        )
        with patch.object(gameplay_attempts, 'receipt_attempt', side_effect=stale_then_real):
            with self.assertRaises(ReplayedClientEventException) as ctx:
                gameplay_attempts.check_attempt(attempt, client_event_id=event_id)
        self.assertEqual(ctx.exception.attempt_id, stored.pk)
        self.assertEqual(Attempt.manager.filter(task=self.ladder).count(), 1)
