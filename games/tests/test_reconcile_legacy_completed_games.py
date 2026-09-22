import json
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command, CommandError
from django.test import TestCase

from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    Project,
    ReplaySlot,
    Task,
    TaskGroup,
    Team,
)


class LegacyCompletionReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        cls.project, _ = Project.objects.get_or_create(id='main')
        cls.user = User.objects.create_user(username='legacy-batch-user')
        cls.other_user = User.objects.create_user(username='legacy-batch-other')
        cls.team = Team.objects.create(name='legacy-batch-team')

    def _task(self, game_id, task_type='alphabetty', label=None):
        game, _ = Game.objects.get_or_create(
            id=game_id,
            defaults={
                'name': game_id,
                'author': 'test',
                'project': self.project,
                'requires_ticket': False,
                'is_tournament': False,
                'is_ready': True,
            },
        )
        group = TaskGroup.objects.create(label=label or game_id)
        task = Task.objects.create(
            task_group=group,
            number='1',
            task_type=task_type,
            checker_id='equals_with_possible_spaces',
            answer='TEST',
        )
        return game, task

    def _state(self, *, team=None, user=None, anon_key=None, game, task, replay_slot=None, complete=True):
        return ChainTaskState.objects.create(
            team=team,
            user=user,
            anon_key=anon_key,
            task=task,
            game=game,
            replay_slot=replay_slot,
            game_mode='general',
            state=json.dumps({'won': complete}),
        )

    def _run(self, *args):
        output = StringIO()
        call_command('reconcile_legacy_completed_games', *args, stdout=output)
        return output.getvalue()

    def test_batch_reconciles_registered_and_anonymous_actors_and_ignores_team_replay(self):
        user_game, user_task = self._task('alphabetty')
        anon_game, anon_task = self._task('alphabetty', 'alphabetty', label='anonymous')
        team_game, team_task = self._task('ladder', 'raddle')
        replay_game, replay_task = self._task('replacements', 'replacements_lines')
        self._state(user=self.user, game=user_game, task=user_task)
        self._state(anon_key='anon-batch-key', game=anon_game, task=anon_task)
        self._state(team=self.team, game=team_game, task=team_task)
        slot = ReplaySlot.objects.create(
            user=self.user,
            actor_key='user:{}'.format(self.user.pk),
            game=replay_game,
            task_group=replay_task.task_group,
        )
        self._state(user=self.user, game=replay_game, task=replay_task, replay_slot=slot)

        output = self._run()

        self.assertIn('created_pcg=2', output)
        self.assertNotIn(self.user.username, output)
        self.assertNotIn('anon-batch-key', output)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)
        self.assertEqual(PlayerCompletedGame.objects.filter(anon_key='anon-batch-key').count(), 1)
        self.assertEqual(PlayerCompletedGame.objects.filter(team=self.team).count(), 0)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user, game=replay_game).count(), 0)
        self.assertEqual(PlayerCompletedGame.objects.filter(is_backfilled=True).count(), 2)

    def test_second_run_is_idempotent_and_preserves_existing_current_row(self):
        game, task = self._task('alphabetty')
        self._state(user=self.user, game=game, task=task)
        current = PlayerCompletedGame.objects.create(
            user=self.user,
            game=game,
            task_group=task.task_group,
            game_kind='alphabetty',
            game_instance_id='alphabetty:{}'.format(task.task_group_id),
            result=PlayerCompletedGame.RESULT_SOLVED,
            is_backfilled=False,
            instrumentation_version=2,
        )

        first = self._run()
        second = self._run()

        self.assertIn('created_pcg=0', first)
        self.assertIn('created_pcg=0', second)
        current.refresh_from_db()
        self.assertFalse(current.is_backfilled)
        self.assertEqual(current.instrumentation_version, 2)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 1)

    def test_incomplete_group_is_reported_without_creating_pcg(self):
        game, _ = Game.objects.get_or_create(
            id='alphabetty',
            defaults={
                'name': 'alphabetty', 'author': 'test', 'project': self.project,
                'requires_ticket': False, 'is_tournament': False, 'is_ready': True,
            },
        )
        group = TaskGroup.objects.create(label='two tasks')
        first = Task.objects.create(task_group=group, number='1', task_type='alphabetty', checker_id='equals_with_possible_spaces')
        Task.objects.create(task_group=group, number='2', task_type='alphabetty', checker_id='equals_with_possible_spaces')
        self._state(user=self.user, game=game, task=first)

        output = self._run()

        self.assertIn('incomplete_candidates=1', output)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user).count(), 0)

    def test_activation_is_backfilled_without_product_goal(self):
        for index in range(3):
            game_id = 'alphabetty'
            task_type = 'alphabetty'
            game, task = self._task(game_id, task_type, label='activation-{}'.format(index))
            self._state(user=self.user, game=game, task=task)

        self._run()

        state = PlayerAnalyticsState.objects.get(user=self.user)
        self.assertIsNotNone(state.activated_at)
        self.assertTrue(state.activation_is_backfilled)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user, is_backfilled=True).count(), 3)
        self.assertEqual(PlayerCompletedGame.objects.filter(user=self.user, metrika_acked_at__isnull=False).count(), 0)

    def test_audit_passes_after_batch_and_detects_missing_row(self):
        game, task = self._task('alphabetty')
        self._state(user=self.user, game=game, task=task)
        self._run()
        output = self._run('--audit')
        self.assertIn('legacy_complete_instances=1', output)
        self.assertIn('missing_pcg=0', output)

        PlayerCompletedGame.objects.filter(user=self.user).delete()
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('reconcile_legacy_completed_games', '--audit', stdout=output)
        self.assertIn('missing_pcg=1', output.getvalue())

    def test_checkpoint_limit_resumes_remaining_actor(self):
        first_game, first_task = self._task('alphabetty')
        second_game, second_task = self._task('alphabetty', 'alphabetty', label='second actor')
        self._state(user=self.user, game=first_game, task=first_task)
        self._state(user=self.other_user, game=second_game, task=second_task)
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = '{}/checkpoint.json'.format(directory)
            first = self._run('--limit', '1', '--checkpoint-file', checkpoint)
            second = self._run('--checkpoint-file', checkpoint)

        self.assertIn('created_pcg=1', first)
        self.assertIn('created_pcg=1', second)
        self.assertEqual(PlayerCompletedGame.objects.filter(is_backfilled=True).count(), 2)

    def test_actor_failure_does_not_stop_remaining_batch(self):
        first_game, first_task = self._task('alphabetty')
        second_game, second_task = self._task('alphabetty', 'alphabetty', label='failure-test')
        self._state(user=self.user, game=first_game, task=first_task)
        self._state(user=self.other_user, game=second_game, task=second_task)

        from games.analytics import reconcile_legacy_completed_games_for_actor as real_reconcile

        def flaky_reconcile(**kwargs):
            if kwargs['user'].pk == self.user.pk:
                raise RuntimeError('synthetic actor failure')
            return real_reconcile(**kwargs)

        with patch(
            'games.management.commands.reconcile_legacy_completed_games.reconcile_legacy_completed_games_for_actor',
            side_effect=flaky_reconcile,
        ):
            output = self._run()

        self.assertIn('actors_failed=1', output)
        self.assertEqual(PlayerCompletedGame.objects.filter(is_backfilled=True).count(), 1)
