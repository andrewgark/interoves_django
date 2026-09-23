"""Collaborative raddle drafts and unused-clue strikethrough."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from games.anon_migrate import migrate_anon_raddle_ui_states
from games.models import (
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    RaddleUiState,
    ReplaySlot,
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

        row = RaddleUiState.objects.get(task=self.task, team=self.team)
        self.assertEqual(row.drafts, {'2': 'CCC'})
        self.assertEqual(row.clue_marks, {'1': True})
        self.assertFalse(ChainTaskState.objects.filter(task=self.task, team=self.team).exists())

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
        row = RaddleUiState.objects.get(task=self.task, team=self.team)
        self.assertEqual(row.drafts, {})
        self.assertFalse(ChainTaskState.objects.filter(task=self.task, team=self.team).exists())

    def test_stale_revision_cannot_overwrite_newer_ui_state(self):
        client = self._client('raddle_ui_one')
        with patch('games.views.raddle_views.track_actor_task_change'):
            newer = client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'NEW'}),
                    'ui_revision': '2',
                },
            )
            stale = client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'OLD'}),
                    'ui_revision': '1',
                },
            )
        self.assertEqual(newer.json()['raddle_ui'][str(self.task.pk)]['revision'], 2)
        self.assertTrue(stale.json()['raddle_ui_stale'])
        row = RaddleUiState.objects.get(task=self.task, team=self.team)
        self.assertEqual(row.revision, 2)
        self.assertEqual(row.drafts, {'2': 'NEW'})

    def test_equal_revision_from_second_tab_cannot_overwrite_first_tab(self):
        client = self._client('raddle_ui_one')
        with patch('games.views.raddle_views.track_actor_task_change'):
            client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'FIRST'}),
                    'ui_revision': '1',
                },
            )
            second_tab = client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'SECOND'}),
                    'ui_revision': '1',
                },
            )
        self.assertTrue(second_tab.json()['raddle_ui_stale'])
        self.assertEqual(
            RaddleUiState.objects.get(task=self.task, team=self.team).drafts,
            {'2': 'FIRST'},
        )

    def test_ui_autosave_does_not_create_or_lock_authoritative_state(self):
        client = self._client('raddle_ui_one')
        with patch.object(
            ChainTaskState.objects,
            'select_for_update',
            side_effect=AssertionError('UI endpoint must not lock ChainTaskState'),
        ), patch('games.views.raddle_views.track_actor_task_change'):
            response = client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'2': 'CCC'}),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChainTaskState.objects.filter(task=self.task, team=self.team).exists())
        self.assertTrue(RaddleUiState.objects.filter(task=self.task, team=self.team).exists())

    def test_legacy_ui_fields_are_read_only_lazy_fallback(self):
        ChainTaskState.objects.create(
            task=self.task,
            game=self.game,
            team=self.team,
            game_mode='general',
            state=json.dumps({
                'solved_indices': [0],
                'used_hints': [1],
                'assist_tier': {'0': 1},
                'total': 2,
                'drafts': {'2': 'LEGACY'},
                'clue_marks': {'1': True},
            }),
        )
        client = self._client('raddle_ui_one')
        with patch('games.views.raddle_views.track_actor_task_change'):
            response = client.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {
                    'game_id': self.game.pk,
                    'drafts': json.dumps({'3': 'NEW'}),
                },
            )
        self.assertEqual(response.status_code, 200)
        chain = ChainTaskState.objects.get(task=self.task, team=self.team)
        self.assertEqual(json.loads(chain.state)['solved_indices'], [0])
        ui = RaddleUiState.objects.get(task=self.task, team=self.team)
        self.assertEqual(ui.drafts, {'2': 'LEGACY', '3': 'NEW'})
        self.assertEqual(ui.clue_marks, {'1': True})

    def test_personal_users_have_separate_ui_state(self):
        first = self._client('raddle_ui_one')
        second = self._client('raddle_ui_two')
        headers = {'HTTP_X_INTEROVES_PLAY_MODE': 'personal'}
        with patch('games.views.raddle_views.track_actor_task_change'):
            first.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {'game_id': self.game.pk, 'drafts': json.dumps({'2': 'ONE'})},
                **headers,
            )
            second.post(
                f'/send_raddle_ui/{self.task.pk}/',
                {'game_id': self.game.pk, 'drafts': json.dumps({'2': 'TWO'})},
                **headers,
            )
        self.assertEqual(
            RaddleUiState.objects.get(task=self.task, user=self.user_one).drafts,
            {'2': 'ONE'},
        )
        self.assertEqual(
            RaddleUiState.objects.get(task=self.task, user=self.user_two).drafts,
            {'2': 'TWO'},
        )

    def test_replay_run_id_keeps_ui_state_namespaces_separate(self):
        slot = ReplaySlot.objects.create(
            team=self.team,
            actor_key='team:{}:{}'.format(self.team.pk, self.game.pk),
            game=self.game,
            task_group=self.task.task_group,
        )
        first_run = slot.run_id
        RaddleUiState.objects.create(
            team=self.team,
            task=self.task,
            game=self.game,
            replay_slot=slot,
            replay_run_id=first_run,
            game_mode='general',
            drafts={'2': 'FIRST'},
        )
        slot.run_id = uuid.uuid4()
        slot.save(update_fields=['run_id'])
        RaddleUiState.objects.create(
            team=self.team,
            task=self.task,
            game=self.game,
            replay_slot=slot,
            replay_run_id=slot.run_id,
            game_mode='general',
            drafts={'2': 'SECOND'},
        )
        self.assertEqual(
            RaddleUiState.objects.filter(team=self.team, replay_slot=slot).count(),
            2,
        )

    def test_anonymous_ui_state_follows_existing_anon_migration(self):
        RaddleUiState.objects.create(
            anon_key='anon-raddle-ui',
            task=self.task,
            game=self.game,
            game_mode='general',
            drafts={'2': 'ANON'},
        )
        self.assertEqual(
            migrate_anon_raddle_ui_states(self.user_one, 'anon-raddle-ui'),
            1,
        )
        row = RaddleUiState.objects.get(task=self.task, user=self.user_one)
        self.assertEqual(row.drafts, {'2': 'ANON'})
