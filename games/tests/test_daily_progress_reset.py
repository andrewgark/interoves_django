from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import TestCase

from games.daily_progress_reset import reset_current_daily_release_progress
from games.models import (
    Attempt, ChainTaskState, DailySolveTiming, Game, GameTaskGroup,
    PlayerCompletedGame, Project, ReplaySlot, Task, TaskGroup,
)


MOSCOW = ZoneInfo('Europe/Moscow')


class DailyProgressResetTests(TestCase):
    def setUp(self):
        self.project = Project.objects.get(id='sections')
        self.game = Game.objects.get(id='ladder')
        self.game.tags = {'ladder_publish_start': '2026-09-21T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])
        self.task_group = TaskGroup.objects.create()
        self.placement, created = GameTaskGroup.objects.get_or_create(
            game=self.game,
            number='2',
            defaults={'task_group': self.task_group, 'name': 'Лесенка #2'},
        )
        if not created:
            self.task_group = self.placement.task_group
        self.task = Task.objects.create(
            task_group=self.task_group,
            number='1',
            task_type='raddle',
            checker_data='{}',
        )
        self.user = User.objects.create_user(username='reset-user')
        self.now = datetime(2026, 9, 22, 1, 0, tzinfo=MOSCOW)

    def _attempt(self, when, replay_slot=None):
        attempt = Attempt.manager.create(
            task=self.task,
            game=self.game,
            user=self.user,
            replay_slot=replay_slot,
            text='{}',
            status='Wrong',
        )
        Attempt.manager.filter(pk=attempt.pk).update(time=when)
        return attempt

    @patch('games.recheck.recheck_chain_task')
    def test_deletes_only_prepublication_attempts_and_rebuilds_chain(self, recheck):
        old = self._attempt(datetime(2026, 9, 21, 23, 59, tzinfo=MOSCOW))
        fresh = self._attempt(datetime(2026, 9, 22, 0, 1, tzinfo=MOSCOW))
        replay_slot = ReplaySlot.objects.create(
            user=self.user,
            actor_key='user:{}'.format(self.user.pk),
            game=self.game,
            task_group=self.task_group,
        )
        old_replay = self._attempt(
            datetime(2026, 9, 21, 23, 58, tzinfo=MOSCOW), replay_slot,
        )
        fresh_replay = self._attempt(
            datetime(2026, 9, 22, 0, 2, tzinfo=MOSCOW), replay_slot,
        )
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{}',
            last_attempt=old,
        )
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            replay_slot=replay_slot,
            game_mode='general',
            state='{}',
            last_attempt=old_replay,
        )

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result[0]['attempts'], 2)
        self.assertFalse(Attempt.manager.filter(pk=old.pk).exists())
        self.assertFalse(Attempt.manager.filter(pk=old_replay.pk).exists())
        self.assertTrue(Attempt.manager.filter(pk=fresh.pk).exists())
        self.assertTrue(Attempt.manager.filter(pk=fresh_replay.pk).exists())
        self.assertEqual(recheck.call_count, 2)
        self.assertEqual(
            {call.kwargs['replay_slot'] for call in recheck.call_args_list},
            {None, replay_slot},
        )

    def test_does_not_reset_a_previous_daily_number(self):
        old = self._attempt(datetime(2026, 9, 21, 23, 59, tzinfo=MOSCOW))
        self.placement.number = '1'
        self.placement.save(update_fields=['number'])

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result, [])
        self.assertTrue(Attempt.manager.filter(pk=old.pk).exists())

    def test_frozen_timing_updated_after_publication_is_preserved(self):
        timing = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            status=DailySolveTiming.STATUS_COMPLETED,
            frozen_ms=12_000,
            completed_at=self.now + timedelta(minutes=1),
        )
        DailySolveTiming.objects.filter(pk=timing.pk).update(
            created_at=self.now - timedelta(days=1),
            updated_at=self.now + timedelta(minutes=1),
        )

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result, [])
        self.assertTrue(DailySolveTiming.objects.filter(pk=timing.pk).exists())

    def test_truly_stale_timing_is_deleted(self):
        timing = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            status=DailySolveTiming.STATUS_AUTO_PAUSED,
        )
        DailySolveTiming.objects.filter(pk=timing.pk).update(
            created_at=self.now - timedelta(days=1),
            updated_at=self.now - timedelta(days=1),
        )

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result[0]['timings'], 1)
        self.assertFalse(DailySolveTiming.objects.filter(pk=timing.pk).exists())

    def test_fresh_completion_is_preserved_and_prepublication_completion_is_deleted(self):
        fresh = PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='raddle',
            game_instance_id='fresh-completion',
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        stale = PlayerCompletedGame.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.task_group,
            game_kind='raddle',
            game_instance_id='stale-completion',
            result=PlayerCompletedGame.RESULT_SOLVED,
        )
        PlayerCompletedGame.objects.filter(pk=fresh.pk).update(
            completed_at=self.now + timedelta(minutes=1),
        )
        PlayerCompletedGame.objects.filter(pk=stale.pk).update(
            completed_at=self.now - timedelta(days=1),
        )

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result[0]['completed'], 1)
        self.assertTrue(PlayerCompletedGame.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(PlayerCompletedGame.objects.filter(pk=stale.pk).exists())

    @patch('games.daily_result_projection.refresh_daily_result_projection')
    def test_projection_rebuild_is_scheduled_after_cleanup_commit(self, rebuild):
        self._attempt(datetime(2026, 9, 21, 23, 59, tzinfo=MOSCOW))

        with self.captureOnCommitCallbacks(execute=True):
            result = reset_current_daily_release_progress(
                now=self.now, game_ids=('ladder',),
            )

        self.assertTrue(result[0]['reset'])
        rebuild.assert_called_once_with(self.game, self.task_group)

    @patch('games.recheck.recheck_chain_task', side_effect=RuntimeError('recheck failed'))
    def test_recheck_failure_rolls_back_cleanup(self, recheck):
        old = self._attempt(datetime(2026, 9, 21, 23, 59, tzinfo=MOSCOW))
        fresh = self._attempt(datetime(2026, 9, 22, 0, 1, tzinfo=MOSCOW))
        ChainTaskState.objects.create(
            user=self.user,
            task=self.task,
            game=self.game,
            game_mode='general',
            state='{}',
            last_attempt=old,
        )

        with self.assertRaises(RuntimeError):
            reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertTrue(Attempt.manager.filter(pk=old.pk).exists())
        self.assertTrue(Attempt.manager.filter(pk=fresh.pk).exists())
        self.assertTrue(ChainTaskState.objects.filter(pk__isnull=False, last_attempt=old).exists())
        recheck.assert_called_once()

    @patch('games.daily_progress_reset.reset_daily_release_progress')
    def test_retries_deadlock_and_continues(self, reset):
        reset.side_effect = [
            OperationalError(1213, 'Deadlock found when trying to get lock'),
            {'reset': False, 'attempts': 0, 'hint_attempts': 0, 'chains': 0},
        ]

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result, [])
        self.assertEqual(reset.call_count, 2)

    @patch('games.daily_progress_reset.reset_daily_release_progress')
    def test_skips_reset_after_repeated_deadlocks(self, reset):
        reset.side_effect = OperationalError(1213, 'Deadlock found when trying to get lock')

        result = reset_current_daily_release_progress(now=self.now, game_ids=('ladder',))

        self.assertEqual(result, [])
        self.assertEqual(reset.call_count, 3)
