import json
import hashlib
import hmac
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from games.models import (
    Attempt,
    ChainTaskState,
    HTMLPage,
    PlayerCompletedGame,
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)
from games.support.services.word_salad import create_word_salad
from games.word_salad_outbox import (
    FakeWordSaladTransport,
    _payload,
    dispatch_word_salad_recheck_outbox,
    reconcile_word_salad_recheck_outbox,
)
from games.word_salad_recheck import (
    enqueue_word_salad_recheck,
    process_word_salad_recheck_item,
    process_word_salad_rechecks,
    serialize_job,
)


class WordSaladRecheckQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='recheck-user')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
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

    def test_superseding_job_cancels_unsent_previous_outbox(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            first = enqueue_word_salad_recheck(task=self.task, game=self.game)
            first_item = WordSaladRecheckItem.objects.get(job=first)
            second = enqueue_word_salad_recheck(task=self.task, game=self.game)

        first.refresh_from_db()
        first_item.refresh_from_db()
        first_outbox = WordSaladRecheckOutbox.objects.get(item=first_item)
        self.assertEqual(first.status, WordSaladRecheckJob.STATUS_SUPERSEDED)
        self.assertEqual(first_item.status, WordSaladRecheckItem.STATUS_SUPERSEDED)
        self.assertEqual(first_outbox.status, WordSaladRecheckOutbox.STATUS_CANCELLED)
        self.assertEqual(WordSaladRecheckItem.objects.filter(job=second).count(), 1)

    def test_targeted_supersede_cancels_unsent_previous_outbox(self):
        actor = (None, self.user.pk, None, None)
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value={actor}):
            first = enqueue_word_salad_recheck(task=self.task, game=self.game)
        first.status = WordSaladRecheckJob.STATUS_RUNNING
        first.save(update_fields=['status', 'updated_at'])
        from games.word_salad_recheck import enqueue_actor_rechecks
        second = enqueue_actor_rechecks(task=self.task, game=self.game, actors=[actor])

        first_item = WordSaladRecheckItem.objects.get(job=first)
        first_outbox = WordSaladRecheckOutbox.objects.get(item=first_item)
        self.assertEqual(first_item.status, WordSaladRecheckItem.STATUS_SUPERSEDED)
        self.assertEqual(first_outbox.status, WordSaladRecheckOutbox.STATUS_CANCELLED)
        self.assertEqual(WordSaladRecheckItem.objects.filter(job=second).count(), 1)

    def test_queued_actor_replays_are_coalesced_and_resolution_is_merged(self):
        from games.word_salad_recheck import enqueue_actor_rechecks
        first_actor = (None, self.user.pk, None, None)
        second_actor = (None, None, 'coalesced-anon', None)
        first = enqueue_actor_rechecks(
            task=self.task,
            game=self.game,
            actors=[first_actor],
            pending_resolution={'version': 1, 'attempt_ids': [101], 'scopes': []},
        )
        second = enqueue_actor_rechecks(
            task=self.task,
            game=self.game,
            actors=[second_actor],
            pending_resolution={'version': 1, 'attempt_ids': [202], 'scopes': []},
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.total_actors, 2)
        self.assertEqual(set(second.pending_resolution['attempt_ids']), {101, 202})
        self.assertEqual(WordSaladRecheckItem.objects.filter(job=second).count(), 2)
        self.assertEqual(WordSaladRecheckOutbox.objects.filter(item__job=second).count(), 2)

    def test_worker_completes_one_item_and_notifies_actor(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(
                task=self.task,
                game=self.game,
                pending_resolution={'version': 1, 'attempt_ids': [123], 'scopes': []},
            )
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
        self.assertEqual(
            recheck.call_args.kwargs['pending_resolution'],
            {'version': 1, 'attempt_ids': [123], 'scopes': []},
        )

    def test_successful_retry_clears_stale_errors(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        job.last_error = 'old transient error'
        job.save(update_fields=['last_error'])
        item.last_error = 'old transient error'
        item.save(update_fields=['last_error'])
        with patch(
            'games.word_salad_recheck.recheck_word_salad_actor',
            return_value={'credited': 0},
        ):
            self.assertEqual(process_word_salad_rechecks(limit=1, worker='test'), 1)
        job.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(job.last_error, '')
        self.assertEqual(item.last_error, '')

    def test_job_serialization_exposes_pending_resolution_scope(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(
                task=self.task,
                game=self.game,
                pending_resolution={
                    'version': 1,
                    'attempt_ids': [123, 456],
                    'scopes': [{'type': 'wall_words', 'value': ['африка']}],
                },
            )
        payload = serialize_job(job)
        self.assertEqual(payload['pending_resolution_version'], 1)
        self.assertEqual(payload['replay_mode'], 'full_recheck')
        self.assertEqual(payload['pending_attempt_count'], 2)
        self.assertEqual(payload['pending_scope_count'], 1)

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

    def test_real_replay_duplicate_delivery_does_not_duplicate_credit_or_projection(self):
        anon_key = 'real-word-salad-duplicate-delivery'
        path = [0, 1, 2, 3, 7, 6, 5, 4, 8, 9, 10, 11, 15, 14, 13, 12]
        Attempt.manager.create(
            anon_key=anon_key,
            task=self.task,
            game=self.game,
            text=json.dumps({'action': 'solve', 'path': path}),
            status='Ok',
            points=0,
        )
        payload = json.loads(self.task.checker_data)
        payload['words'] = list(payload.get('words') or []) + ['ABCD']
        self.task.checker_data = json.dumps(payload, ensure_ascii=False)
        self.task.save(update_fields=['checker_data'])
        with patch('games.views.track.track_actor_task_change'):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
            item = WordSaladRecheckItem.objects.get(job=job)
            before = {
                'attempts': Attempt.manager.filter(task=self.task, anon_key=anon_key).count(),
                'chain': ChainTaskState.objects.filter(task=self.task, anon_key=anon_key).count(),
                'completed': PlayerCompletedGame.objects.filter(
                    task_group=self.task.task_group, game=self.game, anon_key=anon_key,
                ).count(),
            }
            first = process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk)
            after_first = {
                'attempts': Attempt.manager.filter(task=self.task, anon_key=anon_key).count(),
                'chain': ChainTaskState.objects.filter(task=self.task, anon_key=anon_key).count(),
                'completed': PlayerCompletedGame.objects.filter(
                    task_group=self.task.task_group, game=self.game, anon_key=anon_key,
                ).count(),
            }
            second = process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk)
            third = process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk)
        self.assertEqual(first, 'completed')
        self.assertEqual(second, 'completed')
        self.assertEqual(third, 'completed')
        item.refresh_from_db()
        after = {
            'attempts': Attempt.manager.filter(task=self.task, anon_key=anon_key).count(),
            'chain': ChainTaskState.objects.filter(task=self.task, anon_key=anon_key).count(),
            'completed': PlayerCompletedGame.objects.filter(
                task_group=self.task.task_group, game=self.game, anon_key=anon_key,
            ).count(),
        }
        self.assertEqual(item.credited_attempts, 1)
        self.assertEqual(after['attempts'], before['attempts'] + 1)
        self.assertEqual(after_first['attempts'], after['attempts'])
        self.assertEqual(after_first['chain'], after['chain'])
        self.assertEqual(after_first['completed'], after['completed'])

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

    def test_transport_actor_id_is_actor_key_not_item_primary_key(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        outbox = WordSaladRecheckOutbox.objects.get(item__job=job)
        item = outbox.item
        payload = _payload(outbox)
        self.assertEqual(payload['actor_id'], item.actor_key)
        self.assertNotEqual(str(payload['actor_id']), str(item.pk))

    def test_outbox_dispatch_is_safe_to_repeat(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        transport = FakeWordSaladTransport()
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['sent'], 1)
        self.assertEqual(dispatch_word_salad_recheck_outbox(limit=1, transport=transport)['sent'], 0)
        self.assertEqual(len(transport.messages), 1)
        self.assertEqual(transport.messages[0]['item_id'], WordSaladRecheckItem.objects.get(job=job).pk)

    def test_outbox_dispatch_uses_batches_of_at_most_ten(self):
        actors = {(None, self.user.pk, None, None)}
        actors.update(
            (None, None, 'anonymous-actor-{}'.format(index), None)
            for index in range(11)
        )
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        transport = FakeWordSaladTransport()
        batch_sizes = []
        original_send_batch = transport.send_batch

        def send_batch(rows):
            batch_sizes.append(len(rows))
            return original_send_batch(rows)

        transport.send_batch = send_batch
        result = dispatch_word_salad_recheck_outbox(limit=25, transport=transport)

        self.assertEqual(result, {'sent': 12, 'failed': 0})
        self.assertEqual(batch_sizes, [10, 2])
        self.assertEqual(len(transport.messages), 12)
        self.assertEqual(
            WordSaladRecheckOutbox.objects.filter(item__job=job, status='sent').count(),
            12,
        )

    def test_outbox_dispatch_retries_missing_batch_outcome(self):
        actors = {
            (None, self.user.pk, None, None),
            (None, None, 'anonymous-partial', None),
        }
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        rows = list(WordSaladRecheckOutbox.objects.filter(item__job=job).order_by('id'))

        class PartialTransport:
            def send_batch(self, batch):
                return {batch[0].pk: 'accepted'}

        result = dispatch_word_salad_recheck_outbox(limit=2, transport=PartialTransport())

        self.assertEqual(result, {'sent': 1, 'failed': 1})
        rows[0].refresh_from_db()
        rows[1].refresh_from_db()
        self.assertEqual(rows[0].status, WordSaladRecheckOutbox.STATUS_SENT)
        self.assertEqual(rows[1].status, WordSaladRecheckOutbox.STATUS_PENDING)
        self.assertIsNotNone(rows[1].next_attempt_at)

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

    def test_reconciliation_is_read_only_by_default_and_repairs_missing_row(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        WordSaladRecheckOutbox.objects.filter(item=item).delete()

        findings = reconcile_word_salad_recheck_outbox()
        self.assertEqual(findings[0]['kind'], 'missing_outbox')
        self.assertFalse(WordSaladRecheckOutbox.objects.filter(item=item).exists())

        findings = reconcile_word_salad_recheck_outbox(apply=True)
        self.assertTrue(findings[0]['repaired'])
        self.assertTrue(WordSaladRecheckOutbox.objects.filter(item=item).exists())

    def test_reconciliation_reopens_stale_sending_row(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        outbox = WordSaladRecheckOutbox.objects.get(item__job=job)
        outbox.status = WordSaladRecheckOutbox.STATUS_SENDING
        outbox.claimed_until = timezone.now() - timedelta(seconds=1)
        outbox.save(update_fields=['status', 'claimed_until', 'updated_at'])

        findings = reconcile_word_salad_recheck_outbox(apply=True)
        self.assertTrue(any(f['kind'] == 'stale_sending' and f['repaired'] for f in findings))
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, WordSaladRecheckOutbox.STATUS_PENDING)

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

    def test_active_job_lease_serializes_specific_item_deliveries(self):
        actors = {
            (None, self.user.pk, None, None),
            (None, None, 'anonymous-actor', None),
        }
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        items = list(WordSaladRecheckItem.objects.filter(job=job).order_by('id'))
        with patch('games.word_salad_recheck.recheck_word_salad_actor'):
            from games.word_salad_recheck import _claim_specific_item
            self.assertEqual(_claim_specific_item(job_id=job.pk, item_id=items[0].pk)[0], 'claimed')
            self.assertEqual(
                process_word_salad_recheck_item(job_id=job.pk, item_id=items[1].pk),
                'lease_conflict',
            )

    @override_settings(
        RECHECK_JOB_LEASE_SECONDS=90,
        RECHECK_ITEM_LEASE_SECONDS=90,
        WORD_SALAD_JOB_LEASE_SECONDS=90,
        WORD_SALAD_ITEM_LEASE_SECONDS=90,
    )
    def test_worker_leases_are_runtime_configurable(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        now = timezone.now()
        from games.word_salad_recheck import _claim_specific_item
        self.assertEqual(
            _claim_specific_item(job_id=job.pk, item_id=item.pk, now=now)[0],
            'claimed',
        )
        job.refresh_from_db()
        item.refresh_from_db()
        self.assertAlmostEqual((job.claimed_until - now).total_seconds(), 90, delta=2)
        self.assertAlmostEqual((item.claimed_until - now).total_seconds(), 90, delta=2)

    def test_validation_failpoints_are_gated_to_worker_validation_mode(self):
        from games.word_salad_recheck import _validation_failpoint
        with patch.dict('os.environ', {
            'INTEROVES_RUNTIME_ROLE': 'web',
            'INTEROVES_VALIDATION_MODE': '1',
            'INTEROVES_VALIDATION_FAILPOINT': 'crash_after_commit',
        }, clear=False), patch('games.word_salad_recheck.os._exit') as exit_process:
            _validation_failpoint('crash_after_commit')
            exit_process.assert_not_called()

        with patch.dict('os.environ', {
            'INTEROVES_RUNTIME_ROLE': 'worker',
            'INTEROVES_VALIDATION_MODE': '1',
            'INTEROVES_VALIDATION_FAILPOINT': 'crash_after_commit',
        }, clear=False), patch('games.word_salad_recheck.os._exit') as exit_process:
            _validation_failpoint('crash_after_commit')
            exit_process.assert_called_once_with(86)

    @override_settings(ROOT_URLCONF='interoves_django.urls')
    def test_worker_endpoint_requires_hmac_and_accepts_completed_delivery(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        payload = {
            'version': 1,
            'operation': 'recheck',
            'job_id': job.pk,
            'item_id': item.pk,
            'actor_id': item.actor_key,
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

    @override_settings(ROOT_URLCONF='interoves_django.urls')
    def test_worker_endpoint_accepts_native_private_sqsd_delivery_on_worker_role(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)
        payload = {
            'version': 1,
            'operation': 'word_salad_recheck',
            'job_id': job.pk,
            'item_id': item.pk,
            'actor_id': item.actor_key,
            'task_revision': str(job.task_revision),
        }
        with patch.dict('os.environ', {'INTEROVES_RUNTIME_ROLE': 'worker'}, clear=False), \
             patch('games.word_salad_recheck.recheck_word_salad_actor', return_value={'credited': 0}):
            response = Client().post(
                '/internal/worker/recheck/',
                json.dumps(payload).encode(),
                content_type='application/json',
                HTTP_USER_AGENT='aws-sqsd/2.0',
                HTTP_X_AWS_SQSD_MSGID='message-1',
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
