"""MySQL-only concurrency coverage for the Phase 2 Raddle UI boundary."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from django.contrib.auth.models import User

from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    RaddleUiState,
    Task,
    TaskGroup,
    Team,
)


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row-lock semantics')
class RaddleUiMySQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        CheckerType.objects.get_or_create(pk='raddle')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        cls.game = Game.objects.create(
            id='raddle_ui_mysql_concurrency',
            name='Raddle UI MySQL concurrency',
            author='test',
            project_id='sections',
            is_ready=True,
            is_playable=True,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        cls.group = TaskGroup.objects.create(label='raddle_ui_mysql_concurrency')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number=1)
        cls.task = Task.objects.create(
            task_group=cls.group,
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
        cls.team = Team.objects.create(name='raddle_ui_mysql_team')
        cls.user = User.objects.create_user('raddle_ui_mysql_user', password='pw')
        Profile.objects.create(user=cls.user, team_on=cls.team)

    def _post_ui(self, revision='1', value='BBB'):
        close_old_connections()
        client = Client()
        self.assertTrue(client.login(username='raddle_ui_mysql_user', password='pw'))
        try:
            return client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'1': value}),
                    'ui_revision': revision,
                },
                HTTP_X_INTEROVES_PLAY_MODE='team',
            )
        finally:
            close_old_connections()

    def _post_answer(self):
        close_old_connections()
        client = Client()
        self.assertTrue(client.login(username='raddle_ui_mysql_user', password='pw'))
        try:
            return client.post(
                f'/send_attempt/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'word_index': 1,
                    'word': 'BBB',
                },
                HTTP_X_INTEROVES_PLAY_MODE='team',
            )
        finally:
            close_old_connections()

    def test_ui_autosave_and_answer_submission_use_different_rows(self):
        with patch('games.views.raddle_views.track_actor_task_change'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                ui_future = pool.submit(self._post_ui)
                answer_future = pool.submit(self._post_answer)
                ui_response = ui_future.result()
                answer_response = answer_future.result()

        self.assertEqual(ui_response.status_code, 200)
        self.assertEqual(answer_response.status_code, 200)
        self.assertTrue(RaddleUiState.objects.filter(team=self.team).exists())
        chain = ChainTaskState.objects.filter(team=self.team, task=self.task).first()
        self.assertIsNotNone(chain)
        self.assertNotIn('drafts', json.loads(chain.state or '{}'))

    def test_two_ui_autosaves_serialize_on_ui_row_only(self):
        with patch('games.views.raddle_views.track_actor_task_change'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self._post_ui, '1', 'ONE')
                second = pool.submit(self._post_ui, '2', 'TWO')
                first_response = first.result()
                second_response = second.result()
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(ChainTaskState.objects.filter(team=self.team).count(), 0)
        self.assertEqual(RaddleUiState.objects.filter(team=self.team).count(), 1)
