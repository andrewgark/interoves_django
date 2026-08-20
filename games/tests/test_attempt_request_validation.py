import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.utils import timezone

from games.exception import DuplicateAttemptException
from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Hint,
    HintAttempt,
    Project,
    Task,
    TaskGroup,
)
from games.views.attempt_views import process_send_attempt
from games.views.hint_views import create_hint_attempt, send_hint_attempt


class AttemptRequestValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='attempt_validation',
                name='attempt validation',
                author='test',
                project_id='sections',
                is_ready=True,
                start_time=timezone.now() - timedelta(days=1),
                end_time=timezone.now() + timedelta(days=1),
            )
            cls.group = TaskGroup.objects.create(label='attempt_validation_group')
            GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.group, number=1, name='validation',
            )
            cls.wall = Task.objects.create(
                task_group=cls.group, number='1', task_type='wall',
            )
            cls.replacements = Task.objects.create(
                task_group=cls.group, number='2', task_type='replacements_lines',
            )
            cls.raddle = Task.objects.create(
                task_group=cls.group, number='3', task_type='raddle',
            )
            cls.default = Task.objects.create(
                task_group=cls.group,
                number='4',
                task_type='default',
                checker=CheckerType.objects.get(pk='equals_with_possible_spaces'),
                checker_data='answer',
            )
            cls.hint = Hint.objects.create(task=cls.default, number='1', text='hint')

    def _request(self, data):
        request = RequestFactory().post('/send/', dict(data, anon_key='validation-anon'))
        request.user = AnonymousUser()
        return request

    def _process(self, task, data):
        with patch(
            'games.views.attempt_views.game_from_request_for_task',
            return_value=self.game,
        ), patch('games.views.attempt_views._get_play_mode', return_value='personal'):
            return process_send_attempt(self._request(data), task.pk)

    def test_malformed_wall_json_returns_invalid_form(self):
        result = self._process(self.wall, {
            'stage': 'cat_explanation',
            'text': 'explanation',
            'words': '{bad json',
        })
        self.assertEqual(result['status'], 'invalid_form')

    def test_unknown_wall_stage_returns_invalid_form(self):
        result = self._process(self.wall, {
            'stage': 'unknown',
            'words[]': ['a', 'b'],
        })
        self.assertEqual(result['status'], 'invalid_form')

    def test_bad_replacements_index_returns_invalid_form(self):
        result = self._process(self.replacements, {
            'line_index': 'not-an-index',
            'answers': json.dumps(['x']),
        })
        self.assertEqual(result['status'], 'invalid_form')

    def test_bad_raddle_index_returns_invalid_form(self):
        result = self._process(self.raddle, {
            'word_index': 'not-an-index',
            'word': 'answer',
        })
        self.assertEqual(result['status'], 'invalid_form')

    def test_invalid_payloads_do_not_create_attempts(self):
        self.test_malformed_wall_json_returns_invalid_form()
        self.test_bad_replacements_index_returns_invalid_form()
        self.test_bad_raddle_index_returns_invalid_form()
        self.assertFalse(Attempt.manager.filter(game=self.game).exists())

    def test_hint_request_without_number_returns_json_status(self):
        request = self._request({'game_id': self.game.pk})
        with patch(
            'games.views.hint_views.game_from_request_for_task',
            return_value=self.game,
        ), patch('games.views.hint_views._get_play_mode', return_value='personal'):
            response = send_hint_attempt(request, self.default.pk)
        self.assertEqual(json.loads(response.content)['status'], 'invalid_form')

    def test_hint_creation_is_single_save_and_duplicate_safe(self):
        with patch('games.views.track.track_task_change') as track:
            hint_attempt, mode = create_hint_attempt(
                self.hint, anon_key='hint-anon', game=self.game,
            )
        self.assertEqual(mode, 'tournament')
        self.assertTrue(hint_attempt.is_real_request)
        self.assertEqual(HintAttempt.objects.filter(hint=self.hint).count(), 1)
        track.assert_not_called()

        with self.assertRaises(DuplicateAttemptException):
            create_hint_attempt(self.hint, anon_key='hint-anon', game=self.game)
        self.assertEqual(HintAttempt.objects.filter(hint=self.hint).count(), 1)
