"""MySQL/InnoDB verification for the Phase 1-3 structural boundaries.

These tests deliberately use TransactionTestCase and create all rows in
autocommit setup.  Worker threads then reread every object through their own
connection, so a passing test represents real overlapping database sessions.
"""

import json
import os
import time
from datetime import datetime, timedelta
from threading import Barrier, Thread
from unittest import skipUnless
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from games.completion_coordinator import complete_logical_game
from games.daily_progress_reset import reset_current_daily_release_progress
from games.daily_timing import ACTION_HEARTBEAT, apply_timing_event
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    Project,
    Task,
    TaskGroup,
)


@skipUnless(connection.vendor == 'mysql', 'requires MySQL/InnoDB')
class StructuralMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.project, _ = Project.objects.get_or_create(pk='sections')
        self.checker, _ = CheckerType.objects.get_or_create(pk='raddle')
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
                'name': 'Synthetic structural concurrency ladder',
                'author': 'test',
                'project': self.project,
                'is_ready': True,
                'is_playable': True,
                'start_time': timezone.now() - timedelta(days=2),
                'end_time': timezone.now() + timedelta(days=2),
            },
        )
        Game.objects.filter(pk=self.game.pk).update(
            tags={'ladder_publish_start': '2026-09-21T00:00:00+03:00'},
        )
        self.game.refresh_from_db()
        self.user = User.objects.create_user(
            username='structural-mysql-user-{}'.format(uuid4().hex[:10]),
        )
        self.reset_game = self.game
        self.reset_group, self.reset_task = self._group(2, game=self.reset_game)

    def _group(self, number, game=None):
        game = game or self.game
        group = TaskGroup.objects.create(label='structural-mysql-{}'.format(uuid4().hex))
        GameTaskGroup.objects.create(
            game=game, task_group=group, number=str(number), name=str(number),
        )
        task = Task.objects.create(
            task_group=group,
            number='1',
            task_type='raddle',
            checker=self.checker,
            checker_data=json.dumps({
                'lengths': [3], 'hints': [], 'words': ['AAA'],
            }),
            answer='AAA',
        )
        return group, task

    def _complete_fixture(self, number):
        group, task = self._group(number)
        ChainTaskState.objects.create(
            user=self.user,
            task=task,
            game=self.game,
            game_mode='general',
            state=json.dumps({'solved_indices': [0], 'total': 1}),
        )
        DailySolveTiming.objects.create(
            user=self.user, game=self.game, task_group=group,
            status=DailySolveTiming.STATUS_RUNNING,
        )
        return group.pk, task.pk

    def _connection_id(self, ids):
        with connection.cursor() as cursor:
            cursor.execute('SELECT CONNECTION_ID()')
            ids.append(cursor.fetchone()[0])

    def _parallel(self, workers):
        errors = []
        threads = []

        def run(index, worker):
            try:
                worker()
            except Exception as exc:
                errors.append((index, exc))

        for index, worker in enumerate(workers):
            thread = Thread(target=run, args=(index, worker))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(timeout=45)
            self.assertFalse(thread.is_alive(), 'worker thread timed out')
        if errors:
            self._print_mysql_deadlock_diagnostics()
        self.assertEqual(errors, [], [
            'worker {}: {}'.format(index, repr(error))
            for index, error in errors
        ])

    def _print_mysql_deadlock_diagnostics(self):
        """Print the retained InnoDB deadlock report after a failed worker."""
        try:
            with connection.cursor() as cursor:
                cursor.execute('SHOW ENGINE INNODB STATUS')
                row = cursor.fetchone()
                status = row[2] if row and len(row) > 2 else ''
        except Exception as exc:
            print('MYSQL_DEADLOCK_DIAGNOSTICS_ERROR {!r}'.format(exc))
            return
        marker = 'LATEST DETECTED DEADLOCK'
        start = status.find(marker)
        if start < 0:
            print('MYSQL_DEADLOCK_REPORT_NOT_PRESENT')
            return
        end = status.find('\nTRANSACTIONS', start)
        if end < 0:
            end = min(len(status), start + 16000)
        print('MYSQL_LATEST_DETECTED_DEADLOCK\n{}'.format(status[start:end]))

    def _completion_worker(self, group_id, task_id, barrier, ids, results):
        close_old_connections()
        try:
            self._connection_id(ids)
            group = TaskGroup.objects.get(pk=group_id)
            task = Task.objects.get(pk=task_id)
            game = Game.objects.get(pk=self.game.pk)
            user = User.objects.get(pk=self.user.pk)
            barrier.wait(timeout=45)
            started = time.monotonic()
            results.append(complete_logical_game(
                actor={'user': user}, game=game, task_group=group,
                task=task, source='mysql-concurrency',
            ))
            results.append(time.monotonic() - started)
        finally:
            close_old_connections()

    def test_a_duplicate_completion_uses_two_sessions(self):
        for iteration in range(20):
            group_id, task_id = self._complete_fixture(10 + iteration)
            barrier = Barrier(2)
            ids, results = [], []
            self._parallel([
                lambda: self._completion_worker(group_id, task_id, barrier, ids, results),
                lambda: self._completion_worker(group_id, task_id, barrier, ids, results),
            ])
            self.assertEqual(len(set(ids)), 2)
            self.assertEqual(
                PlayerCompletedGame.objects.filter(
                    user=self.user, game=self.game, task_group_id=group_id,
                ).count(), 1,
            )
            timing = DailySolveTiming.objects.get(user=self.user, task_group_id=group_id)
            self.assertEqual(timing.status, DailySolveTiming.STATUS_COMPLETED)
            self.assertIsNotNone(timing.frozen_ms)

    def test_b_completion_and_heartbeat_preserve_frozen_timing(self):
        for iteration in range(20):
            group_id, task_id = self._complete_fixture(30 + iteration)
            barrier = Barrier(2)
            ids, errors, results = [], [], []

            def heartbeat():
                close_old_connections()
                try:
                    self._connection_id(ids)
                    group = TaskGroup.objects.get(pk=group_id)
                    game = Game.objects.get(pk=self.game.pk)
                    user = User.objects.get(pk=self.user.pk)
                    barrier.wait(timeout=45)
                    results.append(apply_timing_event(
                        game=game, task_group=group, user=user,
                        action=ACTION_HEARTBEAT, session_id=uuid4(),
                        event_id='heartbeat-{}'.format(uuid4().hex), seq=1,
                        now=timezone.now(), create=True,
                    ))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    close_old_connections()

            self._parallel([
                lambda: self._completion_worker(group_id, task_id, barrier, ids, results),
                heartbeat,
            ])
            self.assertEqual(errors, [], [repr(error) for error in errors])
            self.assertEqual(len(set(ids)), 2)
            timing = DailySolveTiming.objects.get(user=self.user, task_group_id=group_id)
            self.assertEqual(timing.status, DailySolveTiming.STATUS_COMPLETED)
            self.assertIsNotNone(timing.frozen_ms)

    def _reset_fixture(self, iteration):
        completion_user = User.objects.create_user(
            username='reset-completion-{}-{}'.format(iteration, uuid4().hex[:6]),
        )
        ChainTaskState.objects.create(
            user=completion_user, task=self.reset_task, game=self.reset_game,
            game_mode='general', state=json.dumps({'solved_indices': [0], 'total': 1}),
        )
        DailySolveTiming.objects.create(
            user=completion_user, game=self.reset_game, task_group=self.reset_group,
            status=DailySolveTiming.STATUS_RUNNING,
        )
        # The maintenance reset must have real stale work, but this actor is
        # unrelated to the fresh completion actor.
        stale_anon = 'stale-reset-{}'.format(uuid4().hex)
        stale = Attempt.manager.create(
            task=self.reset_task, game=self.reset_game, anon_key=stale_anon,
            text='{}', status='Wrong',
        )
        Attempt.manager.filter(pk=stale.pk).update(
            time=datetime(2026, 9, 20, 23, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        return self.reset_group.pk, self.reset_task.pk, completion_user.pk, stale_anon

    def _completion_for_user(self, group_id, task_id, user_id, barrier, ids, results):
        close_old_connections()
        try:
            self._connection_id(ids)
            group = TaskGroup.objects.get(pk=group_id)
            task = Task.objects.get(pk=task_id)
            game = Game.objects.get(pk=self.reset_game.pk)
            user = User.objects.get(pk=user_id)
            barrier.wait(timeout=45)
            results.append(complete_logical_game(
                actor={'user': user}, game=game, task_group=group,
                task=task, source='mysql-reset-race',
            ))
        finally:
            close_old_connections()

    def _reset_worker(self, barrier, ids, results):
        close_old_connections()
        try:
            self._connection_id(ids)
            barrier.wait(timeout=45)
            results.append(reset_current_daily_release_progress(
                now=datetime(2026, 9, 22, 1, 0, tzinfo=ZoneInfo('Europe/Moscow')),
                game_ids=('ladder',),
            ))
        finally:
            close_old_connections()

    def test_d_reset_and_completion_preserve_fresh_authoritative_rows(self):
        iterations = 100 if os.environ.get('STRUCTURAL_MYSQL_STRESS') else 20
        for iteration in range(iterations):
            group_id, task_id, user_id, stale_anon = self._reset_fixture(iteration)
            barrier = Barrier(2)
            ids, results = [], []
            self._parallel([
                lambda: self._completion_for_user(
                    group_id, task_id, user_id, barrier, ids, results,
                ),
                lambda: self._reset_worker(barrier, ids, results),
            ])
            self.assertEqual(len(set(ids)), 2)
            self.assertFalse(Attempt.manager.filter(anon_key=stale_anon).exists())
            self.assertTrue(PlayerCompletedGame.objects.filter(
                user_id=user_id, task_group_id=group_id,
            ).exists())
            timing = DailySolveTiming.objects.get(user_id=user_id, task_group_id=group_id)
            self.assertEqual(timing.status, DailySolveTiming.STATUS_COMPLETED)
            self.assertIsNotNone(timing.frozen_ms)
