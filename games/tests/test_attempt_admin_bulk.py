import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from games.attempt_admin_bulk import (
    add_to_checker,
    reject_pending_attempts,
    recheck_chain_attempts,
)
from games.recheck import recheck_chain_task
from games.views.attempt_views import check_attempt
from games.models import ChainTaskState, WordSaladRecheckItem, WordSaladRecheckJob
from games.tests.test_chain_task_state import (
    _ChainFixture,
    _make_attempt,
    _wall_text,
)


class AttemptAdminBulkTests(_ChainFixture, TestCase):
    def test_replay_keeps_unrelated_wall_pending_attempt_pending(self):
        first_words = _make_attempt(
            self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']),
        )
        check_attempt(first_words)
        second_words = _make_attempt(
            self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']),
        )
        check_attempt(second_words)

        accepted = _make_attempt(
            self.wall_task, self.team,
            _wall_text(['A', 'B', 'C', 'D'], stage='cat_explanation', explanation='accepted'),
        )
        accepted.status = 'Pending'
        accepted.possible_status = 'Wrong'
        accepted.save()
        unrelated = _make_attempt(
            self.wall_task, self.team,
            _wall_text(['E', 'F', 'G', 'H'], stage='cat_explanation', explanation='unrelated'),
        )
        unrelated.status = 'Pending'
        unrelated.possible_status = 'Wrong'
        unrelated.save()

        with patch('games.views.track.track_task_change'):
            with self.captureOnCommitCallbacks(execute=True):
                add_to_checker([accepted.pk])

        recheck_chain_task(
            self.wall_task,
            team=self.team,
            game=self.game,
            notify=False,
            pending_resolution={
                'attempt_ids': [accepted.pk],
                'scopes': [{'type': 'wall_words', 'value': ['a', 'b', 'c', 'd']}],
            },
        )

        accepted.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertNotEqual(accepted.status, 'Pending')
        self.assertEqual(unrelated.status, 'Pending')
        self.assertEqual(unrelated.possible_status, 'Wrong')

    def test_wall_checker_add_merges_selected_rows_and_queues_one_job(self):
        first = _make_attempt(
            self.wall_task,
            self.team,
            _wall_text(['A', 'B', 'C', 'D'], stage='cat_explanation', explanation='first'),
        )
        first.status = 'Pending'
        first.save()
        second = _make_attempt(
            self.wall_task,
            self.team2,
            _wall_text(['E', 'F', 'G', 'H'], stage='cat_explanation', explanation='second'),
        )
        second.status = 'Pending'
        second.save()

        with patch('games.views.track.track_task_change'):
            with self.captureOnCommitCallbacks(execute=True):
                add_to_checker([first.pk, second.pk])

        self.wall_task.refresh_from_db()
        payload = json.loads(self.wall_task.checker_data)
        self.assertIn('first', payload['answers'][0]['checker'])
        self.assertIn('second', payload['answers'][1]['checker'])
        jobs = list(WordSaladRecheckJob.objects.filter(task=self.wall_task))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].items.count(), 2)

    def test_chain_recheck_deduplicates_same_actor(self):
        first = _make_attempt(
            self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']),
        )
        first.status = 'Pending'
        first.save()
        second = _make_attempt(
            self.wall_task, self.team, _wall_text(['E', 'F', 'G', 'H']),
        )
        second.status = 'Pending'
        second.save()

        with patch('games.views.track.track_task_change'):
            with self.captureOnCommitCallbacks(execute=True):
                recheck_chain_attempts([first.pk, second.pk])

        jobs = list(WordSaladRecheckJob.objects.filter(task=self.wall_task))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(WordSaladRecheckItem.objects.filter(job=jobs[0]).count(), 1)

    def test_reject_pending_only_changes_status(self):
        attempt = _make_attempt(
            self.wall_task, self.team, _wall_text(['A', 'B', 'C', 'D']),
        )
        attempt.status = 'Pending'
        attempt.possible_status = 'Wrong'
        attempt.points = 0
        attempt.save()
        state = ChainTaskState.objects.create(
            task=self.wall_task,
            game=self.game,
            team=self.team,
            game_mode='general',
            state=json.dumps({'best_points': 1}),
        )

        reject_pending_attempts([attempt.pk])

        attempt.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(attempt.status, 'Wrong')
        self.assertEqual(state.state, json.dumps({'best_points': 1}))

    def test_rejecting_pending_replacement_does_not_advance_chain_state(self):
        now = timezone.now()
        self.game.start_time = now - timedelta(minutes=1)
        self.game.end_time = now + timedelta(minutes=1)
        self.game.save(update_fields=['start_time', 'end_time'])
        state = ChainTaskState.objects.create(
            task=self.repl_task,
            game=self.game,
            team=self.team,
            game_mode='tournament',
            state=json.dumps({'solved_slots': {}, 'solved_lines': [], 'total': 0}),
        )
        state_before = state.state

        pending = _make_attempt(
            self.repl_task, self.team, json.dumps({'line_index': 0, 'answers': ['wrong']}),
        )
        check_attempt(pending)
        self.assertEqual(pending.status, 'Pending')
        self.assertEqual(pending.possible_status, 'Wrong')
        self.assertEqual(pending.state, state_before)

        reject_pending_attempts([pending.pk])

        state.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'Wrong')
        self.assertEqual(state.state, state_before)
