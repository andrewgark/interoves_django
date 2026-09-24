import json
import hashlib
import hmac
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from games.models import WordSaladRecheckItem, WordSaladRecheckJob, WordSaladRecheckOutbox
from games.support.services.word_salad import create_word_salad
from games.word_salad_outbox import FakeWordSaladTransport, dispatch_word_salad_recheck_outbox
from games.word_salad_recheck import (
    enqueue_word_salad_recheck,
    process_word_salad_recheck_item,
    process_word_salad_rechecks,
)


class WordSaladRecheckQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='recheck-user')
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        from games.models import Game, Task
        self.task = Task.objects.get(pk=detail['task_id'])
        self.game = Game.objects.get(pk='salad')

    def test_enqueue_snapshots_actors_and_supersedes_previous_job(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            first = enqueue_word_salad_recheck(task=self.task, game=self.game)
            second = enqueue_word_salad_recheck(task=self.task, game=self.game)

        first.refresh_from_db()
        self.assertEqual(first.status, WordSaladRecheckJob.STATUS_SUPERSEDED)
        self.assertEqual(second.total_actors, 1)
        item = WordSaladRecheckItem.objects.get(job=second)
        self.assertEqual(item.user_id, self.user.pk)
        self.assertEqual(item.status, WordSaladRecheckItem.STATUS_PENDING)

    def test_worker_completes_one_item_and_notifies_actor(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        with patch(
            'games.word_salad_recheck.recheck_word_salad_actor',
            return_value={'credited': 2},
        ) as recheck:
            self.assertEqual(process_word_salad_rechecks(limit=1, worker='test'), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_COMPLETED)
        self.assertEqual(job.completed_actors, 1)
        self.assertEqual(job.credited_attempts, 2)
        recheck.assert_called_once()
        self.assertFalse(recheck.call_args.kwargs['notify'])

    def test_duplicate_item_delivery_is_a_noop(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        with patch('games.word_salad_recheck.recheck_word_salad_actor', return_value={'credited': 2}) as recheck:
            self.assertEqual(process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk), 'completed')
            self.assertEqual(process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk), 'completed')
        recheck.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.credited_attempts, 2)

    def test_enqueue_creates_one_outbox_row_per_item(self):
        actors = {
            (None, self.user.pk, None, None),
            (None, None, 'anonymous-actor', None),
        }
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        self.assertEqual(WordSaladRecheckOutbox.objects.filter(item__job=job).count(), 2)
        self.assertEqual(
            WordSaladRecheckOutbox.objects.filter(item__job=job).values('item_id', 'task_revision').distinct().count(),
            2,
        )

    def test_outbox_dispatch_is_safe_to_repeat(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        transport = FakeWordSaladTransport()
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['sent'], 1)
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['sent'], 0)
        self.assertEqual(len(transport.messages), 1)
        self.assertEqual(transport.messages[0]['item_id'], WordSaladRecheckItem.objects.get(job=job).pk)

    def test_outbox_duplicate_send_after_send_before_mark_is_tolerated(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)

        class SendThenCrashTransport(FakeWordSaladTransport):
            def __init__(self):
                super().__init__()
                self.crashed = False

            def send(self, payload):
                message_id = super().send(payload)
                if not self.crashed:
                    self.crashed = True
                    raise RuntimeError('crash after send')
                return message_id

        transport = SendThenCrashTransport()
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['failed'], 1)
        WordSaladRecheckOutbox.objects.filter(item__job=job).update(next_attempt_at=None)
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['sent'], 1)
        self.assertEqual(len(transport.messages), 2)

    def test_replay_failure_keeps_item_retryable(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        with patch('games.word_salad_recheck.recheck_word_salad_actor', side_effect=RuntimeError('transient')):
            self.assertEqual(process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk), 'failed')
        item.refresh_from_db()
        self.assertEqual(item.status, WordSaladRecheckItem.STATUS_PENDING)
        self.assertEqual(item.attempt_count, 1)

    def test_stale_revision_is_superseded_without_replay(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        self.task.attempt_revision = uuid.uuid4()
        self.task.save(update_fields=['attempt_revision'])
        item = WordSaladRecheckItem.objects.get(job=job)
        with patch('games.word_salad_recheck.recheck_word_salad_actor') as recheck:
            self.assertEqual(process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk), 'superseded')
        recheck.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.status, WordSaladRecheckItem.STATUS_SUPERSEDED)

    @override_settings(ROOT_URLCONF='interoves_django.urls')
    def test_worker_endpoint_requires_hmac_and_accepts_completed_delivery(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        payload = {
            'version': 1,
            'operation': 'word_salad_recheck',
            'job_id': job.pk,
            'item_id': item.pk,
            'actor_id': item.pk,
            'task_revision': str(job.task_revision),
        }
        body = json.dumps(payload, separators=(',', ':')).encode()
        self.assertEqual(Client().post('/internal/worker/word-salad-recheck/', body, content_type='application/json').status_code, 403)
        timestamp = '2000000000'
        signature = hmac.new(b'test-secret', timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        with patch.dict('os.environ', {'WORD_SALAD_WORKER_HMAC_SECRET': 'test-secret'}), \
             patch('games.word_salad_recheck.recheck_word_salad_actor', return_value={'credited': 0}):
            with patch('games.views.word_salad_worker.time.time', return_value=2000000000):
                response = Client().post(
                    '/internal/worker/word-salad-recheck/', body,
                    content_type='application/json',
                    HTTP_X_INTEROVES_WORKER_TIMESTAMP=timestamp,
                    HTTP_X_INTEROVES_WORKER_SIGNATURE=signature,
                )
        self.assertEqual(response.status_code, 200)

    def test_worker_can_claim_next_item_without_waiting_for_job_lease(self):
        actors = {
            (None, self.user.pk, None, None),
            (None, None, 'anonymous-actor', None),
        }
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        with patch('games.word_salad_recheck.recheck_word_salad_actor', return_value={'credited': 0}):
            self.assertEqual(process_word_salad_rechecks(limit=1, worker='test'), 1)
            job.refresh_from_db()
            self.assertEqual(job.status, WordSaladRecheckJob.STATUS_PENDING)
            self.assertIsNone(job.claimed_until)
            self.assertEqual(process_word_salad_rechecks(limit=1, worker='test'), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_COMPLETED)
        self.assertEqual(job.completed_actors, 2)
