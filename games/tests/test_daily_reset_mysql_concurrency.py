"""Real-connection concurrency coverage for the maintenance daily reset."""

from datetime import datetime
from threading import Barrier, Thread
from unittest import skipUnless
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from games.daily_progress_reset import reset_current_daily_release_progress
from games.daily_timing import ACTION_HEARTBEAT, apply_timing_event
from games.models import (
    Attempt, CheckerType, DailySolveTiming, Game, GameTaskGroup, HTMLPage,
    Project, Task, TaskGroup,
)


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row-lock semantics')
class DailyResetMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.project = Project.objects.get_or_create(pk='sections')[0]
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        self.game, _ = Game.objects.get_or_create(
            id='ladder',
            defaults={
                'name': 'Synthetic ladder', 'author': 'test', 'project': self.project,
                'is_ready': True, 'is_playable': True,
            },
        )
        Game.objects.filter(pk=self.game.pk).update(
            tags={'ladder_publish_start': '2026-09-21T00:00:00+03:00'},
        )
        self.game.refresh_from_db()
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

    def _parallel(self, workers):
        errors = []
        threads = []

        def run(worker):
            try:
                worker()
            except Exception as exc:
                errors.append(exc)

        for worker in workers:
            thread = Thread(target=run, args=(worker,))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), 'worker thread timed out')
        self.assertEqual(errors, [], errors)

    def test_two_reset_calls_share_one_release_lock(self):
        for iteration in range(20):
            old = Attempt.manager.create(
                task=self.task, game=self.game,
                anon_key='reset-concurrent-{}'.format(iteration),
                text='{}', status='Wrong',
            )
            Attempt.manager.filter(pk=old.pk).update(
                time=datetime(2026, 9, 21, 23, 59, tzinfo=ZoneInfo('Europe/Moscow')),
            )
            barrier = Barrier(2)
            results = []
            ids = []

            def reset():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT CONNECTION_ID()')
                        ids.append(cursor.fetchone()[0])
                    barrier.wait(timeout=30)
                    results.append(reset_current_daily_release_progress(
                        now=self.now, game_ids=('ladder',),
                    ))
                finally:
                    close_old_connections()

            self._parallel([reset, reset])

            self.assertEqual(len(set(ids)), 2)
            self.assertLessEqual(sum(bool(result) for result in results), 1)
            self.assertFalse(Attempt.manager.filter(pk=old.pk).exists())

    def test_reset_and_timing_event_do_not_leave_stale_timing(self):
        for iteration in range(20):
            anon_key = 'reset-timing-concurrent-{}'.format(iteration)
            timing = DailySolveTiming.objects.create(
                anon_key=anon_key, game=self.game, task_group=self.group,
            )
            DailySolveTiming.objects.filter(pk=timing.pk).update(
                created_at=datetime(2026, 9, 21, 23, 0, tzinfo=ZoneInfo('Europe/Moscow')),
                updated_at=datetime(2026, 9, 21, 23, 0, tzinfo=ZoneInfo('Europe/Moscow')),
            )

            barrier = Barrier(2)
            ids = []

            def heartbeat():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT CONNECTION_ID()')
                        ids.append(cursor.fetchone()[0])
                    barrier.wait(timeout=30)
                    apply_timing_event(
                        game=Game.objects.get(pk=self.game.pk),
                        task_group=TaskGroup.objects.get(pk=self.group.pk),
                        anon_key=anon_key,
                        action=ACTION_HEARTBEAT,
                        session_id=uuid4(),
                        event_id='concurrent-heartbeat-{}'.format(iteration),
                        seq=1,
                        now=self.now,
                        create=True,
                    )
                finally:
                    close_old_connections()

            def reset():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT CONNECTION_ID()')
                        ids.append(cursor.fetchone()[0])
                    barrier.wait(timeout=30)
                    reset_current_daily_release_progress(
                        now=self.now, game_ids=('ladder',),
                    )
                finally:
                    close_old_connections()

            self._parallel([reset, heartbeat])

            self.assertEqual(len(set(ids)), 2)
            row = DailySolveTiming.objects.filter(
                game=self.game, task_group=self.group, anon_key=anon_key,
            ).first()
            if row is not None:
                self.assertGreaterEqual(row.updated_at, self.now)
