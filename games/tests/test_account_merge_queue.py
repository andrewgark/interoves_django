from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from allauth.socialaccount.models import SocialAccount

from games.account_merge_queue import enqueue_account_merge, run_account_merge_job
from games.models import AccountMerge, AccountMergeJob


class AccountMergeQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.target = User.objects.create_user(username='queue-target')
        self.source = User.objects.create_user(username='queue-source')
        SocialAccount.objects.create(
            user=self.source, provider='telegram', uid='telegram-source',
        )

    @patch('games.account_merge_queue.schedule_account_merge_event')
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

    @patch('games.account_merge_queue.merge_accounts')
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
