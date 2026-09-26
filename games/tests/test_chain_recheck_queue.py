import json
from unittest.mock import patch

from django.test import TestCase

from games.models import ChainTaskState, WordSaladRecheckItem, WordSaladRecheckJob, WordSaladRecheckOutbox
from games.recheck import recheck_team_task_all_chronological
from games.tests.test_chain_task_state import (
    _ChainFixture,
    _make_attempt,
    _repl_text,
    _wall_text,
)
from games.views.attempt_views import check_attempt
from games.word_salad_recheck import process_word_salad_recheck_item


class ChainRecheckQueueTests(_ChainFixture, TestCase):
    def test_chronological_recheck_is_queued_and_the_worker_rebuilds_it(self):
        attempt = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(attempt)
        row = ChainTaskState.objects.get(task=self.repl_task, team=self.team, game_mode='general')
        row.state = json.dumps({'solved_lines': [], 'total': 0})
        row.save(update_fields=['state'])

        with patch('games.recheck.recheck_chain_task') as inline:
            job = recheck_team_task_all_chronological(None, attempt.id)
        inline.assert_not_called()

        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_PENDING)
        self.assertEqual(job.total_actors, 1)
        item = WordSaladRecheckItem.objects.get(job=job)
        self.assertEqual(item.team_id, self.team.pk)
        self.assertTrue(WordSaladRecheckOutbox.objects.filter(item=item).exists())

        with patch('games.word_salad_recheck.recheck_word_salad_actor') as salad:
            state = process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk, worker='test')
        salad.assert_not_called()
        self.assertEqual(state, 'completed')

        job.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_COMPLETED)
        self.assertIn(0, json.loads(row.state)['solved_lines'])

    def test_wall_uses_the_same_queue(self):
        attempt = _make_attempt(self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']))
        check_attempt(attempt)
        job = recheck_team_task_all_chronological(None, attempt.id)
        item = job.items.get()
        state = process_word_salad_recheck_item(job_id=job.pk, item_id=item.pk, worker='test')
        self.assertEqual(state, 'completed')
        job.refresh_from_db()
        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_COMPLETED)
        self.assertTrue(ChainTaskState.objects.filter(task=self.wall_task, team=self.team).exists())

    def test_a_second_enqueue_replaces_only_that_actor(self):
        attempt = _make_attempt(self.repl_task, self.team, _repl_text(0, ['answer1']))
        check_attempt(attempt)
        other = _make_attempt(self.repl_task, self.team2, _repl_text(0, ['answer1']))
        check_attempt(other)

        first = recheck_team_task_all_chronological(None, attempt.id)
        other_job = recheck_team_task_all_chronological(None, other.id)
        second = recheck_team_task_all_chronological(None, attempt.id)

        first.refresh_from_db()
        other_job.refresh_from_db()
        first_item = WordSaladRecheckItem.objects.get(job=first)
        other_item = WordSaladRecheckItem.objects.get(job=other_job)
        self.assertEqual(first.status, WordSaladRecheckJob.STATUS_SUPERSEDED)
        self.assertEqual(first_item.status, WordSaladRecheckItem.STATUS_SUPERSEDED)
        self.assertEqual(other_job.status, WordSaladRecheckJob.STATUS_PENDING)
        self.assertEqual(other_item.status, WordSaladRecheckItem.STATUS_PENDING)
        self.assertEqual(second.items.get().status, WordSaladRecheckItem.STATUS_PENDING)
