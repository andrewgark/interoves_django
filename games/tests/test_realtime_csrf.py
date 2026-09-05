import json
import re
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

CSRF_INPUT_RE = re.compile(
    r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\']\s+name=["\']csrfmiddlewaretoken["\']'
)


def _csrf_from_html(html):
    match = CSRF_INPUT_RE.search(html or '')
    if not match:
        return ''
    return match.group(1) or match.group(2) or ''


class RealtimeTeammateCsrfTests(TestCase):
    """Live team HTML is rendered for the submitter and applied on every page.

    Django prefers POST csrfmiddlewaretoken over X-CSRFToken, so planting
    teammate A's token in B's form makes B's next fetch() a 403 HTML page
    («Ошибка сети»). The client must keep using B's page token.
    """

    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        CheckerType.objects.get_or_create(pk='replacements_lines')
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

        now = timezone.now()
        cls.game = Game.objects.create(
            id='realtime_csrf',
            name='Realtime csrf',
            author='test',
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        cls.group = TaskGroup.objects.create(label='Realtime csrf replacements')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.group, number=1)
        with patch('games.views.track.track_task_change'):
            cls.task = Task.objects.create(
                task_group=cls.group,
                number='1',
                task_type='replacements_lines',
                text='FOO\nBAR',
                checker_id='replacements_lines',
                checker_data=json.dumps({'lines': [['FOO'], ['BAR']]}),
                points=2,
            )

        cls.team = Team.objects.create(name='realtime_csrf_team')
        cls.user_one = User.objects.create_user('csrf_one', password='pw')
        cls.user_two = User.objects.create_user('csrf_two', password='pw')
        Profile.objects.create(user=cls.user_one, team_on=cls.team)
        Profile.objects.create(user=cls.user_two, team_on=cls.team)

    def csrf_client(self, username):
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=username, password='pw'))
        page = client.get(f'/games/{self.game.pk}/1/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('csrftoken', client.cookies)
        return client

    def post_line(self, client, line_index, answer, csrf_token):
        return client.post(
            f'/send_attempt/{self.task.pk}/',
            {
                'csrfmiddlewaretoken': csrf_token,
                'game_id': self.game.pk,
                'line_index': str(line_index),
                'answers[]': answer,
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_submitter_html_csrf_is_rejected_for_teammate(self):
        first = self.csrf_client('csrf_one')
        second = self.csrf_client('csrf_two')
        first_token = first.cookies['csrftoken'].value
        second_token = second.cookies['csrftoken'].value
        self.assertNotEqual(first_token, second_token)

        with patch('games.views.attempt_views.track_actor_task_change'):
            submit = self.post_line(first, 0, 'FOO', first_token)
        self.assertEqual(submit.status_code, 200)
        payload = submit.json()
        self.assertEqual(payload['status'], 'ok')
        fragment = payload['update_task_html_new'][str(self.task.pk)]
        planted = _csrf_from_html(fragment)
        self.assertTrue(planted)
        self.assertNotEqual(planted, second_token)

        poisoned = self.post_line(second, 1, 'BAR', planted)
        self.assertEqual(poisoned.status_code, 403)

        with patch('games.views.attempt_views.track_actor_task_change'):
            own_token = self.post_line(second, 1, 'BAR', second_token)
        self.assertEqual(own_token.status_code, 200)
        self.assertEqual(own_token.json()['status'], 'ok')
