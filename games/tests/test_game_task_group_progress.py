import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup, Attempt
from games.views.new_ui import (
    _game_page_progress_context,
    _task_group_rows_skeleton,
    _game_task_group_links,
)


def _ensure_min_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    CheckerType.objects.get_or_create(pk='replacements_lines')


class GameTaskGroupProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def setUp(self):
        self.client = Client()

    def _create_section_game(self, game_id):
        return Game.objects.create(
            id=game_id,
            name='Section',
            author='a',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )

    def test_section_hub_builds_skeleton_and_progress_url(self):
        game = self._create_section_game('sec_prog')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=json.dumps({'lines': [['a']]}),
                text='',
            )

        factory = RequestFactory()
        request = factory.get('/section/sec_prog/')
        request.user = AnonymousUser()
        request.session = {}
        request.COOKIES['interoves_anon'] = 'test-anon-page-key'

        task_groups = _game_task_group_links(game)
        rows = _task_group_rows_skeleton(task_groups, game)
        progress_ctx = _game_page_progress_context(request, game, 'personal')

        self.assertIsNone(rows[0]['progress_text'])
        self.assertEqual(rows[0]['number'], '1')
        self.assertTrue(progress_ctx['load_task_group_progress'])
        self.assertIn('/games/sec_prog/progress/', progress_ctx['task_group_progress_url'])

    def test_progress_api_reports_actor_progress(self):
        game = self._create_section_game('sec_prog2')
        anon_key = 'test-anon-progress-key'
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg2')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='2', name='Two')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=json.dumps({'lines': [['a'], ['b'], ['c']]}),
                text='',
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text='x',
                status='Partial',
                points=6,
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/games/sec_prog2/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['2']
        self.assertEqual(row['n_solved'], 1)
        self.assertTrue(row['is_fully_solved'])
        # Полностью решено — прогресс-текст не пишем.
        self.assertIsNone(row['progress_text'])

    def test_progress_api_without_actor_returns_empty(self):
        game = self._create_section_game('sec_prog3')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg3')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')

        resp = self.client.get('/games/sec_prog3/progress/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['rows'], {})
