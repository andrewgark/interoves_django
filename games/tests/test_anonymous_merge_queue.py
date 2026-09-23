from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from games.analytics_identity import attach_anon_cookie
from games.anonymous_merge import (
    claim_next_merge_job,
    process_merge_job,
    retry_merge_job,
    run_anonymous_merge_queue,
)
from games.models import (
    AnonymousMergeJob,
    AnonymousMergeReconcileItem,
    Attempt,
    CheckerType,
    Game,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
)
from games.anonymous_merge import enqueue_anonymous_merge


class AnonymousMergeQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        checker = CheckerType.objects.get_or_create(pk='equals')[0]
        cls.game = Game.objects.create(
            id='anonymous_merge_queue_test',
            name='Anonymous merge queue',
            author='test',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )
        cls.groups = []
        cls.tasks = []
        for number in range(3):
            group = TaskGroup.objects.create(label='merge-queue-group-{}'.format(number))
            task = Task.objects.create(
                task_group=group,
                number='1',
                checker=checker,
                points=1,
                answer='ok',
            )
            cls.groups.append(group)
            cls.tasks.append(task)
        cls.user = User.objects.create_user('merge_queue_user', 'merge-queue@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='Q', last_name='U')

    def setUp(self):
        self.client = Client()
        self.assertTrue(self.client.login(username='merge_queue_user', password='secret'))

    def _key(self, suffix='main'):
        return 'merge-queue-{}-{}'.format(self._testMethodName, suffix)[:64]

    def _enqueue(self, key=None):
        key = key or self._key()
        attach_anon_cookie(self.client, key)
        job, status = enqueue_anonymous_merge(self.user, key)
        self.assertEqual(status, 'ok')
        return job

    def _drain(self, job, *, max_operations=100):
        claimed = claim_next_merge_job(worker='test')
        self.assertIsNotNone(claimed)
        claimed_job, token = claimed
        self.assertEqual(claimed_job.pk, job.pk)
        process_merge_job(claimed_job, token, max_operations=max_operations)
        job.refresh_from_db()
        return job

    def test_http_claim_is_fast_and_returns_persistent_job(self):
        key = self._key('http')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='ok', status='Ok')
        attach_anon_cookie(self.client, key)

        response = self.client.post(reverse('new_migrate_anon_attempts'), {'anon_key': key})

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload['queued'])
        self.assertEqual(AnonymousMergeJob.objects.get(pk=payload['job']['id']).status, 'pending')
        self.assertEqual(Attempt.manager.filter(anon_key=key).count(), 1)

    def test_1200_submissions_bulk_move_and_resume(self):
        key = self._key('large')
        Attempt.manager.bulk_create([
            Attempt(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
            for _ in range(1201)
        ])
        job = self._enqueue(key)

        self.assertEqual(job.total_submissions, 0)
        job = self._drain(job)

        self.assertEqual(job.status, AnonymousMergeJob.STATUS_COMPLETED)
        self.assertEqual(job.total_submissions, 1201)
        self.assertEqual(job.moved_submissions, 1201)
        self.assertEqual(job.completed_reconciliation_units, 1)
        self.assertFalse(Attempt.manager.filter(anon_key=key).exists())
        self.assertEqual(Attempt.manager.filter(user=self.user, task=self.tasks[0]).count(), 1201)

    def test_many_reconciliation_units_are_materialized_once(self):
        key = self._key('units')
        Attempt.manager.bulk_create([
            Attempt(anon_key=key, task=task, game=self.game, text='x', status='Wrong')
            for task in self.tasks for _ in range(2)
        ])
        job = self._enqueue(key)
        job = self._drain(job)

        self.assertEqual(job.total_reconciliation_units, 3)
        self.assertEqual(AnonymousMergeReconcileItem.objects.filter(job=job).count(), 3)
        self.assertEqual(AnonymousMergeReconcileItem.objects.filter(job=job, status='completed').count(), 3)

    def test_duplicate_claim_reuses_one_job(self):
        key = self._key('duplicate')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        first, first_status = enqueue_anonymous_merge(self.user, key)
        second, second_status = enqueue_anonymous_merge(self.user, key)

        self.assertEqual((first_status, second_status), ('ok', 'ok'))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AnonymousMergeJob.objects.filter(anon_key=key).count(), 1)

    def test_same_job_cannot_be_claimed_by_two_runners(self):
        key = self._key('runner')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)

        first = claim_next_merge_job(worker='one')
        first[0].refresh_from_db()
        self.assertGreater(first[0].claimed_until, timezone.now())
        self.assertIsNotNone(first)
        self.assertIsNone(claim_next_merge_job(worker='two'))

    def test_expired_lease_can_be_resumed(self):
        key = self._key('lease')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)
        first = claim_next_merge_job(worker='one')
        self.assertIsNotNone(first)
        AnonymousMergeJob.objects.filter(pk=job.pk).update(
            claimed_until=timezone.now() - timedelta(seconds=1),
        )

        second = claim_next_merge_job(worker='two')

        self.assertIsNotNone(second)
        self.assertNotEqual(first[1], second[1])

    def test_retry_after_worker_exception(self):
        key = self._key('retry')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)
        original = __import__('games.anonymous_merge', fromlist=['migrate_anon_history_step']).migrate_anon_history_step
        with patch('games.anonymous_merge.migrate_anon_history_step', side_effect=RuntimeError('temporary')):
            job = self._drain(job, max_operations=2)
        self.assertEqual(job.status, AnonymousMergeJob.STATUS_PENDING)
        AnonymousMergeJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        with patch('games.anonymous_merge.migrate_anon_history_step', side_effect=original):
            job = self._drain(job)
        self.assertEqual(job.status, AnonymousMergeJob.STATUS_COMPLETED)

    def test_retry_resets_exhausted_reconcile_items(self):
        key = self._key('retry-item')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)
        item = AnonymousMergeReconcileItem.objects.create(
            job=job,
            game=self.game,
            task_group=self.groups[0],
            status=AnonymousMergeReconcileItem.STATUS_FAILED,
            attempt_count=5,
            last_error='temporary failure',
        )
        job.status = AnonymousMergeJob.STATUS_FAILED
        job.save(update_fields=['status', 'updated_at'])

        retried = retry_merge_job(self.user, job.pk)

        self.assertEqual(retried.status, AnonymousMergeJob.STATUS_PENDING)
        item.refresh_from_db()
        self.assertEqual(item.status, AnonymousMergeReconcileItem.STATUS_PENDING)
        self.assertEqual(item.attempt_count, 0)
        self.assertEqual(item.last_error, '')

    def test_status_endpoint_is_user_scoped_and_reports_actual_counters(self):
        key = self._key('status')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)

        response = self.client.get(reverse('new_anon_merge_job_status', kwargs={'job_id': job.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['job']['total_submissions'], 0)
        self.assertIn('progress', response.json()['job'])

    def test_reconcile_item_unique_constraint(self):
        key = self._key('unique')
        Attempt.manager.create(anon_key=key, task=self.tasks[0], game=self.game, text='x', status='Wrong')
        job = self._enqueue(key)
        AnonymousMergeReconcileItem.objects.create(job=job, game=self.game, task_group=self.groups[0])
        with self.assertRaises(Exception):
            AnonymousMergeReconcileItem.objects.create(job=job, game=self.game, task_group=self.groups[0])
