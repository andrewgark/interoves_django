"""Production-backend concurrency checks.

These tests intentionally skip SQLite: SQLite locking semantics cannot prove
the row-lock/fencing behavior used by RDS MySQL.  Run them with RDS-like
MySQL settings and the normal Django test database lifecycle.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, close_old_connections
from django.test import TransactionTestCase

from games.models import WordSaladRecheckItem, WordSaladRecheckJob
from games.support.services.word_salad import create_word_salad
from games.word_salad_outbox import _claim_one
from games.word_salad_recheck import enqueue_word_salad_recheck, process_word_salad_recheck_item


@skipUnless(connection.vendor == 'mysql', 'requires an independent MySQL backend')
class WordSaladMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username='mysql-concurrency-user')
        from games.models import HTMLPage
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        from games.models import Game, Task
        self.task = Task.objects.get(pk=detail['task_id'])
        self.game = Game.objects.get(pk='salad')

    @staticmethod
    def _connection_id():
        with connection.cursor() as cursor:
            cursor.execute('SELECT CONNECTION_ID()')
            return cursor.fetchone()[0]

    def _parallel_delivery(self, job, item_ids):
        barrier = Barrier(len(item_ids))
        connection_ids = set()

        def deliver(item_id):
            close_old_connections()
            try:
                connection_ids.add(self._connection_id())
                barrier.wait(timeout=15)
                return process_word_salad_recheck_item(
                    job_id=job.pk, item_id=item_id, worker='mysql-concurrency-test',
                )
            finally:
                close_old_connections()

        with patch('games.word_salad_recheck.recheck_word_salad_actor', return_value={'credited': 0}) as replay:
            with ThreadPoolExecutor(max_workers=len(item_ids)) as pool:
                results = list(pool.map(deliver, item_ids))
        return results, connection_ids, replay

    def test_same_item_has_one_replay_across_independent_mysql_connections(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        item = WordSaladRecheckItem.objects.get(job=job)

        results, connection_ids, replay = self._parallel_delivery(job, [item.pk, item.pk])

        self.assertGreaterEqual(len(connection_ids), 2)
        self.assertEqual(replay.call_count, 1)
        self.assertTrue(all(result in ('completed', 'lease_conflict') for result in results))
        item.refresh_from_db()
        self.assertEqual(item.status, WordSaladRecheckItem.STATUS_COMPLETED)

    def test_different_items_serialize_on_job_lease_and_finish_after_retry(self):
        actors = {
            (None, self.user.pk, None, None),
            (None, None, 'mysql-concurrency-anon', None),
        }
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        items = list(WordSaladRecheckItem.objects.filter(job=job).order_by('id'))

        results, connection_ids, replay = self._parallel_delivery(job, [item.pk for item in items])

        self.assertGreaterEqual(len(connection_ids), 2)
        self.assertEqual(replay.call_count, 1)
        self.assertIn('lease_conflict', results)
        remaining = WordSaladRecheckItem.objects.get(
            job=job, status=WordSaladRecheckItem.STATUS_PENDING,
        )
        self.assertEqual(
            process_word_salad_recheck_item(job_id=job.pk, item_id=remaining.pk),
            'completed',
        )
        self.assertEqual(
            WordSaladRecheckItem.objects.filter(
                job=job, status=WordSaladRecheckItem.STATUS_COMPLETED,
            ).count(),
            2,
        )
        job.refresh_from_db()
        self.assertEqual(job.status, WordSaladRecheckJob.STATUS_COMPLETED)

    def test_two_dispatchers_claim_one_outbox_row(self):
        actors = {(None, self.user.pk, None, None)}
        with patch('games.word_salad_recheck._word_salad_actor_keys', return_value=actors):
            job = enqueue_word_salad_recheck(task=self.task, game=self.game)
        barrier = Barrier(2)
        connection_ids = set()

        def claim():
            close_old_connections()
            try:
                connection_ids.add(self._connection_id())
                barrier.wait(timeout=15)
                row = _claim_one()
                return row.pk if row is not None else None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _unused: claim(), (1, 2)))
        self.assertGreaterEqual(len(connection_ids), 2)
        claimed = [value for value in results if value is not None]
        self.assertEqual(len(claimed), 1)
