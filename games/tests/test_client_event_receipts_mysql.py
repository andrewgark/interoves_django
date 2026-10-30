"""MySQL-only: concurrent resends of one client event produce one attempt."""
import json
import uuid
from datetime import timedelta
from threading import Barrier, Thread
from unittest import skipUnless

from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from games.models import (
    Attempt,
    CheckerType,
    ClientEventReceipt,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
)

WORKERS = 4


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row-lock semantics')
class ClientEventReceiptMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        Project.objects.get_or_create(pk='sections', defaults={})
        CheckerType.objects.get_or_create(pk='raddle')
        # TaskGroup's default checker; TransactionTestCase flushes it between tests.
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        self.game = Game.objects.create(
            id='client_event_mysql', name='Client event MySQL', author='test', project_id='sections',
            is_ready=True, is_playable=True,
            start_time=now - timedelta(days=1), end_time=now + timedelta(days=1),
        )
        group = TaskGroup.objects.create(label='client_event_mysql')
        GameTaskGroup.objects.create(game=self.game, task_group=group, number=1)
        self.task = Task.objects.create(
            task_group=group, number='1', task_type='raddle',
            checker=CheckerType.objects.get(pk='raddle'), points=1,
            checker_data=json.dumps({'lengths': [3, 3, 3], 'hints': ['A ____', '____ C'],
                                     'words': ['AAA', 'BBB', 'CCC']}),
            answer='AAA\nBBB\nCCC',
        )
        self.user = User.objects.create_user('client_event_mysql_user', password='pw')
        Profile.objects.create(user=self.user)

    def _resend_concurrently(self, word, event_id):
        barrier = Barrier(WORKERS)
        responses, errors = [], []

        def worker():
            close_old_connections()
            try:
                client = Client()
                assert client.login(username='client_event_mysql_user', password='pw')
                barrier.wait(timeout=30)
                responses.append(client.post(
                    f'/send_attempt/{self.task.pk}/',
                    {'game_id': self.game.pk, 'word_index': 1, 'word': word, 'client_event_id': event_id},
                    HTTP_X_INTEROVES_PLAY_MODE='personal',
                ).json())
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=worker) for _ in range(WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive(), 'worker thread timed out')
        self.assertEqual(errors, [], [repr(error) for error in errors])
        return responses

    def test_concurrent_resends_of_a_correct_word_record_one_attempt(self):
        event_id = str(uuid.uuid4())
        responses = self._resend_concurrently('BBB', event_id)
        self.assertEqual(Attempt.manager.filter(task=self.task, user=self.user).count(), 1)
        self.assertEqual(ClientEventReceipt.objects.filter(client_event_id=event_id).count(), 1)
        self.assertEqual([r['status'] for r in responses], ['ok'] * WORKERS, responses)
        self.assertTrue(all(r['raddle_correct'] for r in responses), responses)
        self.assertEqual(sum(1 for r in responses if r.get('client_event_replayed')), WORKERS - 1)

    def test_concurrent_resends_of_a_wrong_word_record_one_attempt(self):
        event_id = str(uuid.uuid4())
        responses = self._resend_concurrently('ZZZ', event_id)
        self.assertEqual(Attempt.manager.filter(task=self.task, user=self.user).count(), 1)
        self.assertEqual([r['status'] for r in responses], ['ok'] * WORKERS, responses)
        self.assertFalse(any(r['raddle_correct'] for r in responses), responses)
