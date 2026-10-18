"""Keep large puzzle payloads out of per-attempt history queries."""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from games.models import Attempt, CheckerType, Task, TaskGroup


class AttemptHistoryLoadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        checker, _ = CheckerType.objects.get_or_create(pk='censorly')
        with patch('games.views.track.track_task_change'):
            group = TaskGroup.objects.create(label='history-loading', points=1)
            cls.task = Task.objects.create(
                task_group=group, number='1', checker=checker,
                task_type='censorly', tags={'puzzle': 'large article payload'},
            )
        cls.attempts = [
            Attempt.manager.create(
                task=cls.task, anon_key='history-loading', text=str(index),
                status='Wrong',
            )
            for index in range(3)
        ]
        Attempt.manager.create(
            task=cls.task, anon_key='another-actor', text='other', status='Wrong',
        )
        Attempt.manager.create(
            task=cls.task, anon_key='history-loading', text='skip',
            status='Wrong', skip=True,
        )

    def test_loaded_task_is_shared_without_selecting_its_payload_per_attempt(self):
        with CaptureQueriesContext(connection) as queries:
            attempts = Attempt.manager.get_all_attempts(
                None, self.task, anon_key='history-loading',
            )
            for attempt in attempts:
                self.assertIs(attempt.task, self.task)
                self.assertEqual(attempt.task.tags, self.task.tags)

        self.assertEqual([row.pk for row in attempts], [row.pk for row in self.attempts])
        self.assertEqual(len(queries), 1)
        self.assertNotIn('JOIN', queries[0]['sql'].upper())
        self.assertNotIn('"games_task"', queries[0]['sql'])

    def test_task_id_callers_keep_eager_task_access(self):
        with self.assertNumQueries(1):
            attempts = Attempt.manager.get_all_attempts(
                None, self.task.pk, anon_key='history-loading',
            )
            self.assertEqual(len(attempts), 3)
            for attempt in attempts:
                self.assertEqual(attempt.task.tags, self.task.tags)
