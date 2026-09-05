"""Collaborative raddle drafts and unused-clue strikethrough."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)

LONG_LADDER = {
    'lengths': [3, 3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D', '____ E'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'],
}


class RaddleUiSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        CheckerType.objects.get_or_create(pk='raddle')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

        now = timezone.now()
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='raddle_ui_sync',
                name='Raddle UI sync',
                author='test',
                is_ready=True,
                is_playable=True,
                is_tournament=False,
                start_time=now - timedelta(hours=1),
                end_time=now + timedelta(hours=1),
            )
            group = TaskGroup.objects.create(label='raddle_ui_sync')
            GameTaskGroup.objects.create(game=cls.game, task_group=group, number=1)
            cls.task = Task.objects.create(
                task_group=group,
                number='1',
                task_type='raddle',
                checker_id='raddle',
                checker_data=json.dumps(LONG_LADDER),
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
                points=1,
            )
        cls.team = Team.objects.create(name='raddle_ui_sync_team')
        cls.user_one = User.objects.create_user('raddle_ui_one', password='pw')
        cls.user_two = User.objects.create_user('raddle_ui_two', password='pw')
        Profile.objects.create(user=cls.user_one, team_on=cls.team)
        Profile.objects.create(user=cls.user_two, team_on=cls.team)

    def _client(self, username):
        client = Client()
        self.assertTrue(client.login(username=username, password='pw'))
        return client

    def test_draft_and_clue_mark_persist_without_task_html(self):
        first = self._client('raddle_ui_one')
        published = []

        def capture_track(task, **kwargs):
            published.append(kwargs)

        with patch('games.views.raddle_views.track_actor_task_change', side_effect=capture_track):
            response = first.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'CCC'}),
                    'clue_marks': json.dumps({'1': True}),
                },
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['raddle_ui'][str(self.task.pk)]['drafts'], {'2': 'CCC'})
        self.assertEqual(payload['raddle_ui'][str(self.task.pk)]['clue_marks'], {'1': True})
        self.assertNotIn('update_task_html_new', payload)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].get('reason'), 'raddle.ui_state')
        self.assertNotIn('update_task_html_new', published[0].get('update_html') or {})

        row = ChainTaskState.objects.get(task=self.task, team=self.team)
        state = json.loads(row.state)
        self.assertEqual(state['drafts'], {'2': 'CCC'})
        self.assertEqual(state['clue_marks'], {'1': True})

        second = self._client('raddle_ui_two')
        page = second.get(f'/games/{self.game.pk}/1/')
        self.assertEqual(page.status_code, 200)
        html = page.content.decode('utf-8')
        self.assertIn('data-raddle-server-draft="CCC"', html)
        self.assertIn('new-raddle-clue--struck', html)

    def test_empty_draft_clears_key(self):
        first = self._client('raddle_ui_one')
        with patch('games.views.raddle_views.track_actor_task_change'):
            first.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'CCC'}),
                },
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
            first.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': ''}),
                },
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        row = ChainTaskState.objects.get(task=self.task, team=self.team)
        state = json.loads(row.state)
        self.assertNotIn('drafts', state)
