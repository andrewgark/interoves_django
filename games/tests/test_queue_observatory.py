from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from games.models import (
    Game,
    Profile,
    HTMLPage,
    Task,
    TaskGroup,
    Team,
    WordSaladRecheckItem,
    WordSaladRecheckJob,
    WordSaladRecheckOutbox,
)
from games.support.constants import SUPPORT_CONSOLE_GROUP


class QueueObservatoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.game = Game.objects.create(id='observatory-game', name='Observatory Game')
        cls.task_group = TaskGroup.objects.create(label='Observatory')
        cls.task = Task.objects.create(
            task_group=cls.task_group,
            number='1',
            task_type='word_salad',
            checker_data='{}',
        )
        cls.team = Team.objects.create(name='observatory-team', visible_name='Observatory Team')
        cls.user = User.objects.create_user('observatory-user', password='secret')
        Profile.objects.create(user=cls.user)
        cls.staff = User.objects.create_user('observatory-staff', password='secret')
        Profile.objects.create(user=cls.staff)
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)

    def setUp(self):
        self.client = Client()
        self.job = WordSaladRecheckJob.objects.create(
            task=self.task,
            game=self.game,
            total_actors=1,
            next_attempt_at=timezone.now(),
        )
        self.item = WordSaladRecheckItem.objects.create(
            job=self.job,
            actor_key='[1,2,null,null]',
            team=self.team,
            user=None,
            next_attempt_at=timezone.now(),
        )
        self.outbox = WordSaladRecheckOutbox.objects.create(
            item=self.item,
            task_revision=self.job.task_revision,
        )

    def test_unauthorized_access_is_denied(self):
        response = self.client.get(reverse('support:queue_observatory_summary'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/support/login/', response.url)

    def test_dashboard_uses_json_observatory_shell(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('support:queues'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'queue-observatory')
        self.assertContains(response, '/support/queues/api/summary/')
        self.assertContains(response, 'setInterval(load, 3000)')
        self.assertNotContains(response, 'DOMParser')

    def test_summary_exposes_domain_counts_and_lease_thresholds(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('support:queue_observatory_summary'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()['summary']
        self.assertEqual(payload['jobs']['pending'], 1)
        self.assertEqual(payload['items']['pending'], 1)
        self.assertEqual(payload['outbox']['pending'], 1)
        self.assertEqual(payload['leases_seconds']['job'], 600)
        self.assertIsNotNone(payload['oldest_pending_job_at'])
        self.assertIsNotNone(payload['oldest_pending_job_age_seconds'])

    def test_job_list_is_paginated_and_contains_detail_url(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('support:queue_observatory_jobs'),
            {'status': WordSaladRecheckJob.STATUS_PENDING, 'page_size': 1},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['jobs']), 1)
        self.assertEqual(payload['jobs'][0]['id'], self.job.pk)
        self.assertIn('/support/queues/api/jobs/{}/'.format(self.job.pk), payload['jobs'][0]['url'])
        self.assertEqual(payload['jobs'][0]['counts']['pending'], 1)
        self.assertEqual(payload['jobs'][0]['counts']['completed'], 0)

    def test_job_detail_contains_item_outbox_and_waiting_explanation(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('support:queue_observatory_job_detail', kwargs={'job_id': self.job.pk}),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()['job']
        self.assertEqual(payload['items'][0]['id'], self.item.pk)
        self.assertEqual(payload['outbox'][0]['id'], self.outbox.pk)
        self.assertEqual(payload['explanation']['code'], 'waiting_for_dispatch')
        self.assertEqual(payload['items'][0]['explanation']['code'], 'waiting_for_dispatch')

    def test_canonical_job_page_contains_items_and_outbox(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('support:queue_observatory_job_page', kwargs={'job_id': self.job.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Queue job #{}'.format(self.job.pk))
        self.assertContains(response, 'Items')
        self.assertContains(response, 'Outbox')
        self.assertContains(response, '#{}'.format(self.item.pk))

    def test_canonical_job_page_requires_support_access(self):
        response = self.client.get(
            reverse('support:queue_observatory_job_page', kwargs={'job_id': self.job.pk}),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/support/login/', response.url)

    def test_running_expired_lease_is_explained_as_stale(self):
        self.job.status = WordSaladRecheckJob.STATUS_RUNNING
        self.job.claimed_until = timezone.now() - timedelta(seconds=1)
        self.job.save(update_fields=['status', 'claimed_until', 'updated_at'])
        self.item.status = WordSaladRecheckItem.STATUS_RUNNING
        self.item.claimed_until = timezone.now() - timedelta(seconds=1)
        self.item.save(update_fields=['status', 'claimed_until', 'updated_at'])
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('support:queue_observatory_job_detail', kwargs={'job_id': self.job.pk}),
        )
        payload = response.json()['job']
        self.assertEqual(payload['explanation']['code'], 'job_lease_expired')
        self.assertEqual(payload['items'][0]['explanation']['code'], 'item_lease_expired')
