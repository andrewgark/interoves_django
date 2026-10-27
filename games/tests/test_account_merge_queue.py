from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from games.identity.merge.account_queue import (
    _claim_job,
    _mark_failed,
    enqueue_account_merge,
    run_account_merge_job,
)
from games.models import AccountMerge, AccountMergeJob


class AccountMergeQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.target = User.objects.create_user(username='queue-target')
        self.source = User.objects.create_user(username='queue-source')
        SocialAccount.objects.create(
            user=self.source, provider='telegram', uid='telegram-source',
        )

    @patch('games.identity.merge.account_queue.schedule_account_merge_event')
    def test_enqueue_is_idempotent(self, schedule):
        first = enqueue_account_merge(
            target_user=self.target,
            source_user=self.source,
            provider='telegram',
            provider_uid='telegram-source',
        )
        second = enqueue_account_merge(
            target_user=self.target,
            source_user=self.source,
            provider='telegram',
            provider_uid='telegram-source',
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AccountMergeJob.objects.count(), 1)
        schedule.assert_called_once_with(first.pk)

    @patch('games.identity.merge.account_queue.merge_accounts')
    def test_worker_completes_and_duplicate_delivery_is_safe(self, merge):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
        )
        def complete(**kwargs):
            return AccountMerge.objects.create(
                target_user=self.target,
                source_user=self.source,
                target_user_id_snapshot=self.target.pk,
                source_user_id_snapshot=self.source.pk,
            )

        merge.side_effect = complete

        result = run_account_merge_job(job.pk, worker='test')
        duplicate = run_account_merge_job(job.pk, worker='test')

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(duplicate['status'], 'completed')
        self.assertEqual(merge.call_count, 1)
        job.refresh_from_db()
        self.assertEqual(job.status, AccountMergeJob.STATUS_COMPLETED)
        self.assertIsNotNone(job.account_merge_id)

    @patch('games.identity.merge.account_queue.merge_accounts')
    @patch('games.identity.merge.account_queue.reconcile_account_merge')
    def test_retry_finishes_reconcile_of_committed_merge(self, reconcile, merge):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
        )
        existing = AccountMerge.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            reconcile_pairs=[['ladder', 1]],
        )
        reconcile.side_effect = RuntimeError('db away')

        with patch('games.identity.merge.account_queue.schedule_account_merge_event'):
            failed = run_account_merge_job(job.pk, worker='test')

        self.assertEqual(failed['status'], 'retry_scheduled')
        job.refresh_from_db()
        self.assertEqual(job.status, AccountMergeJob.STATUS_PENDING)

        reconcile.side_effect = None
        AccountMergeJob.objects.filter(pk=job.pk).update(next_attempt_at=None)
        result = run_account_merge_job(job.pk, worker='test')

        self.assertEqual(result, {'status': 'completed', 'merge_id': existing.pk})
        reconcile.assert_called_with(existing)
        merge.assert_not_called()

    def test_expired_worker_cannot_overwrite_new_claim(self):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
        )
        claimed, state = _claim_job(job.pk, worker='old')
        self.assertEqual(state, 'claimed')

        AccountMergeJob.objects.filter(pk=job.pk).update(
            claim_token='22222222-2222-2222-2222-222222222222',
        )
        self.assertFalse(_mark_failed(job.pk, 'stale failure', claimed.claim_token))
        job.refresh_from_db()
        self.assertEqual(job.status, AccountMergeJob.STATUS_RUNNING)
        self.assertEqual(str(job.claim_token), '22222222-2222-2222-2222-222222222222')

    @patch('games.ops.telegram.bot.notify.publish_admin_alert', return_value=True)
    def test_final_failure_alerts_admin(self, publish):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
        )
        claimed, _ = _claim_job(job.pk, worker='test')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(_mark_failed(job.pk, 'merge exploded', claimed.claim_token))
        publish.assert_called_once()
        text = publish.call_args.kwargs['payload']['text']
        self.assertIn('queue-target', text)
        self.assertIn('merge exploded', text)

    def test_status_page_renders_for_owner(self):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
            next_url='/profile/',
        )
        client = Client()
        client.force_login(self.target)
        response = client.get(reverse('ui_account_merge_status', args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Профили объединяются')

    @patch('games.identity.merge.account_queue.schedule_account_merge_event')
    def test_failed_status_can_be_retried(self, schedule):
        job = AccountMergeJob.objects.create(
            target_user=self.target,
            source_user=self.source,
            target_user_id_snapshot=self.target.pk,
            source_user_id_snapshot=self.source.pk,
            provider='telegram',
            provider_uid='telegram-source',
            status=AccountMergeJob.STATUS_FAILED,
            next_url='/profile/',
        )
        client = Client()
        client.force_login(self.target)
        response = client.post(reverse('ui_account_merge_retry', args=[job.pk]))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, AccountMergeJob.STATUS_PENDING)
        schedule.assert_called_once_with(job.pk)
