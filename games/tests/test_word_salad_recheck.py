from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from games.models import WordSaladRecheckItem, WordSaladRecheckJob
from games.support.services.word_salad import create_word_salad
from games.word_salad_recheck import enqueue_word_salad_recheck, process_word_salad_rechecks


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
        self.assertTrue(recheck.call_args.kwargs['notify'])

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
