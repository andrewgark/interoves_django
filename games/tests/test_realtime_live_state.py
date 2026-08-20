from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from games.models import (
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


class RealtimeLiveStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        checker, _ = CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

        now = timezone.now()
        cls.game = Game.objects.create(
            id='realtime_live_state',
            name='Realtime live state',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        cls.group = TaskGroup.objects.create(label='Realtime live state')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number=1)
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                text='Two-session task',
                answer='RIGHT',
                checker=checker,
                points=1,
            )

        cls.team = Team.objects.create(name='realtime_live_state_team')
        cls.user_one = User.objects.create_user('live_state_one', password='pw')
        cls.user_two = User.objects.create_user('live_state_two', password='pw')
        Profile.objects.create(user=cls.user_one, team_on=cls.team)
        Profile.objects.create(user=cls.user_two, team_on=cls.team)

    def logged_in_client(self, username):
        client = Client()
        self.assertTrue(client.login(username=username, password='pw'))
        return client

    def test_task_page_exposes_authoritative_live_state_url(self):
        client = self.logged_in_client('live_state_two')
        response = client.get('/games/realtime_live_state/1/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-track-live-state-url="/games/realtime_live_state/live-state/"',
        )

    def test_teammate_snapshot_contains_solved_task_projection(self):
        first = self.logged_in_client('live_state_one')
        second = self.logged_in_client('live_state_two')

        with patch('games.views.attempt_views.track_actor_task_change'):
            submit = first.post(
                f'/send_attempt/{self.task.pk}/',
                {'game_id': self.game.pk, 'text': 'RIGHT'},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        self.assertEqual(submit.status_code, 200)
        self.assertEqual(submit.json()['attempt_status'], 'Ok')

        snapshot = second.get(
            f'/games/{self.game.pk}/live-state/',
            {'task_ids': str(self.task.pk)},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(snapshot.status_code, 200)
        data = snapshot.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['reload_required'])
        self.assertIn(f'game:{self.game.pk}:team:{self.team.pk}', data['versions'])
        fragment = data['update_task_html_new'][str(self.task.pk)]
        self.assertIn(f'id="new-task-{self.task.pk}"', fragment)
        self.assertIn('data-solved="1"', fragment)

    def test_snapshot_rejects_task_from_another_game(self):
        other_game = Game.objects.create(
            id='realtime_other_game',
            name='Other game',
            author='test',
            is_ready=True,
            is_playable=True,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=1),
        )
        other_group = TaskGroup.objects.create(label='Other live state')
        GameTaskGroup.objects.create(game=other_game, task_group=other_group, number=1)
        with patch('games.views.track.track_task_change'):
            other_task = Task.objects.create(
                task_group=other_group,
                number='1',
                text='Other task',
                answer='RIGHT',
                checker_id='equals_with_possible_spaces',
            )

        client = self.logged_in_client('live_state_two')
        response = client.get(
            f'/games/{self.game.pk}/live-state/',
            {'task_ids': str(other_task.pk)},
        )
        self.assertEqual(response.status_code, 404)

    def test_snapshot_rejects_invalid_task_ids(self):
        client = self.logged_in_client('live_state_two')
        response = client.get(
            f'/games/{self.game.pk}/live-state/',
            {'task_ids': 'not-an-id'},
        )
        self.assertEqual(response.status_code, 400)
