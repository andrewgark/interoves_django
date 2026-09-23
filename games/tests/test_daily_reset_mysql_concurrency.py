"""Real-connection concurrency coverage for the maintenance daily reset."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest import skipUnless
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from games.daily_progress_reset import reset_current_daily_release_progress
from games.daily_timing import ACTION_HEARTBEAT, apply_timing_event
from games.models import Attempt, DailySolveTiming, Game, GameTaskGroup, Project, Task, TaskGroup


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row-lock semantics')
class DailyResetMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        Project.objects.get_or_create(pk='sections')
        self.game = Game.objects.get(pk='ladder')
        self.game.tags = {'ladder_publish_start': '2026-09-21T00:00:00+03:00'}
        self.game.save(update_fields=['tags'])
        self.group = TaskGroup.objects.create(label='mysql-reset-concurrency')
        self.placement = GameTaskGroup.objects.create(
            game=self.game, task_group=self.group, number='2', name='Reset concurrency',
        )
        self.task = Task.objects.create(
            task_group=self.group, number='1', task_type='raddle', checker_data='{}',
        )
        self.now = datetime(2026, 9, 22, 1, 0, tzinfo=ZoneInfo('Europe/Moscow'))

    def _run(self, function):
        close_old_connections()
        try:
            return function()
        finally:
            close_old_connections()

    def test_two_reset_calls_share_one_release_lock(self):
        old = Attempt.manager.create(
            task=self.task, game=self.game, anon_key='reset-concurrent',
            text='{}', status='Wrong',
        )
        Attempt.manager.filter(pk=old.pk).update(
            time=datetime(2026, 9, 21, 23, 59, tzinfo=ZoneInfo('Europe/Moscow')),
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _index: self._run(
                    lambda: reset_current_daily_release_progress(
                        now=self.now, game_ids=('ladder',),
                    )
                ),
                (1, 2),
            ))

        self.assertLessEqual(sum(bool(result) for result in results), 1)
        self.assertFalse(Attempt.manager.filter(pk=old.pk).exists())

    def test_reset_and_timing_event_do_not_leave_stale_timing(self):
        timing = DailySolveTiming.objects.create(
            anon_key='reset-timing-concurrent',
            game=self.game,
            task_group=self.group,
        )
        DailySolveTiming.objects.filter(pk=timing.pk).update(
            created_at=datetime(2026, 9, 21, 23, 0, tzinfo=ZoneInfo('Europe/Moscow')),
            updated_at=datetime(2026, 9, 21, 23, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )

        def heartbeat():
            return apply_timing_event(
                game=self.game,
                task_group=self.group,
                anon_key='reset-timing-concurrent',
                action=ACTION_HEARTBEAT,
                session_id=uuid4(),
                event_id='concurrent-heartbeat',
                seq=1,
                now=self.now,
                create=True,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._run,
                    lambda: reset_current_daily_release_progress(
                        now=self.now, game_ids=('ladder',),
                    ),
                ),
                pool.submit(self._run, heartbeat),
            ]
            [future.result() for future in futures]

        row = DailySolveTiming.objects.filter(
            game=self.game, task_group=self.group, anon_key='reset-timing-concurrent',
        ).first()
        if row is not None:
            self.assertGreaterEqual(row.updated_at, self.now)
