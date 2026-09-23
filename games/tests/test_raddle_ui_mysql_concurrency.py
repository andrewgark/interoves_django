"""MySQL-only concurrency coverage for the Phase 2 Raddle UI boundary."""
import json
from datetime import timedelta
from threading import Barrier, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from django.contrib.auth.models import User

from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    ProfileTeamMembership,
    Project,
    RaddleUiState,
    Task,
    TaskGroup,
    Team,
)


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row-lock semantics')
class RaddleUiMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        Project.objects.get_or_create(pk='sections', defaults={})
        CheckerType.objects.get_or_create(pk='raddle')
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        self.game = Game.objects.create(
            id='raddle_ui_mysql_concurrency',
            name='Raddle UI MySQL concurrency',
            author='test',
            project_id='sections',
            is_ready=True,
            is_playable=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        self.group = TaskGroup.objects.create(label='raddle_ui_mysql_concurrency')
        GameTaskGroup.objects.create(game=self.game, task_group=self.group, number=1)
        self.task = Task.objects.create(
            task_group=self.group,
            number='1',
            task_type='raddle',
            checker=CheckerType.objects.get(pk='raddle'),
            points=1,
            checker_data=json.dumps({
                'lengths': [3, 3, 3],
                'hints': ['A ____', '____ C'],
                'words': ['AAA', 'BBB', 'CCC'],
            }),
            answer='AAA\nBBB\nCCC',
        )
        self.team = Team.objects.create(
            name='raddle_ui_mysql_team', project=Project.objects.get(pk='sections'),
        )
        self.user = User.objects.create_user('raddle_ui_mysql_user', password='pw')
        profile = Profile.objects.create(user=self.user, team_on=self.team)
        ProfileTeamMembership.objects.get_or_create(profile=profile, team=self.team)

    def _run_workers(self, workers, errors):
        def run(worker):
            try:
                worker()
            except Exception as exc:
                errors.append(exc)

        threads = [Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), 'worker thread timed out')
        self.assertEqual(errors, [], [repr(error) for error in errors])

    def _allow_gameplay_context(self, request, **kwargs):
        request.interoves_gameplay_actor_kind = 'user'
        return None

    def _post_ui(self, revision='1', value='BBB', barrier=None, errors=None, ids=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT CONNECTION_ID()')
                if ids is not None:
                    ids.append(cursor.fetchone()[0])
            client = Client()
            assert client.login(username='raddle_ui_mysql_user', password='pw')
            if barrier is not None:
                barrier.wait(timeout=30)
            return client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'1': value}),
                    'ui_revision': revision,
                },
                HTTP_X_INTEROVES_PLAY_MODE='personal',
            )
        finally:
            close_old_connections()

    def _post_answer(self, barrier=None, errors=None, ids=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT CONNECTION_ID()')
                if ids is not None:
                    ids.append(cursor.fetchone()[0])
            client = Client()
            assert client.login(username='raddle_ui_mysql_user', password='pw')
            if barrier is not None:
                barrier.wait(timeout=30)
            return client.post(
                f'/send_attempt/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'word_index': 1,
                    'word': 'BBB',
                },
                HTTP_X_INTEROVES_PLAY_MODE='personal',
            )
        finally:
            close_old_connections()

    def test_ui_autosave_and_answer_submission_use_different_rows(self):
        barrier = Barrier(2)
        errors = []
        ids = []
        results = []
        with patch('games.views.raddle_views.track_actor_task_change'), \
             patch('games.views.raddle_views.validate_gameplay_context', side_effect=self._allow_gameplay_context), \
             patch('games.views.attempt_views.validate_gameplay_context', side_effect=self._allow_gameplay_context):
            self._run_workers([
                lambda: results.append(self._post_ui(barrier=barrier, errors=errors, ids=ids)),
                lambda: results.append(self._post_answer(barrier=barrier, errors=errors, ids=ids)),
            ], errors)

        self.assertEqual(len(set(ids)), 2)
        self.assertEqual([response.status_code for response in results], [200, 200])
        ui_state = RaddleUiState.objects.get(task=self.task, game=self.game)
        self.assertEqual(ui_state.user_id, self.user.pk)
        self.assertTrue(Attempt.manager.filter(task=self.task, user=self.user).exists())
        chain = ChainTaskState.objects.filter(user=self.user, task=self.task).first()
        if chain is not None:
            self.assertNotIn('drafts', json.loads(chain.state or '{}'))

    def test_two_ui_autosaves_serialize_on_ui_row_only(self):
        barrier = Barrier(2)
        errors = []
        ids = []
        results = []
        with patch('games.views.raddle_views.track_actor_task_change'), \
             patch('games.views.raddle_views.validate_gameplay_context', side_effect=self._allow_gameplay_context), \
             patch('games.views.attempt_views.validate_gameplay_context', side_effect=self._allow_gameplay_context):
            self._run_workers([
                lambda: results.append(self._post_ui('1', 'ONE', barrier, errors, ids)),
                lambda: results.append(self._post_ui('2', 'TWO', barrier, errors, ids)),
            ], errors)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual([response.status_code for response in results], [200, 200])
        self.assertEqual(ChainTaskState.objects.filter(user=self.user).count(), 0)
        ui_state = RaddleUiState.objects.get(task=self.task, game=self.game)
        self.assertEqual(ui_state.user_id, self.user.pk)
