from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from games.models import (
    CheckerType,
    Game,
    GameResultsSnapshot,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)
from games.views.new_ui import _results_table_headers_context, new_section_results_page


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


class SectionResultsSplitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def setUp(self):
        self.factory = RequestFactory()

    def _create_section_game(self, game_id='sec_res'):
        return Game.objects.create(
            id=game_id,
            name='Section',
            author='a',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )

    def test_initial_page_skips_bulk_attempts(self):
        game = self._create_section_game()
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='x',
                text='y',
            )

        request = self.factory.get('/section/sec_res/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.models.Attempt.manager.get_bulk_game_actor_rows') as bulk_mock:
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, 'sec_res')
                bulk_mock.assert_not_called()
                ctx = render_mock.call_args[0][2]
                self.assertEqual(ctx['teams_sorted'], [])
                self.assertTrue(ctx['progressive_results'])
                self.assertEqual(len(ctx['task_groups']), 1)

    def test_partial_page_loads_rows(self):
        game = self._create_section_game('sec_res2')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg2')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='x',
                text='y',
            )

        request = self.factory.get('/section/sec_res2/results/?page=1&partial=1')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.models.Attempt.manager.get_bulk_game_actor_rows') as bulk_mock:
            bulk_mock.return_value = {}
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, 'sec_res2')
                bulk_mock.assert_called_once()
                self.assertEqual(render_mock.call_args[0][1], 'new/partials/results_rows.html')

    def test_headers_context_without_snapshot(self):
        game = self._create_section_game('sec_res3')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg3')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='2', name='Two')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='a',
                text='b',
            )

        ctx = _results_table_headers_context(game)
        self.assertEqual(len(ctx['task_groups']), 1)
        self.assertEqual(ctx['task_groups'][0].number, '2')
        self.assertEqual(len(ctx['task_group_to_tasks']['2']), 1)

    def test_initial_with_snapshot_uses_headers_only(self):
        game = self._create_section_game('sec_res4')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg4')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='x',
                text='y',
            )

        GameResultsSnapshot.objects.create(
            game=game,
            mode='general',
            payload={
                'task_groups': [{'number': '1', 'name': 'One', 'tasks': [{'number': '1'}]}],
                'rows': [],
            },
        )

        request = self.factory.get('/section/sec_res4/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.results_snapshot.snapshot_to_results_context') as full_snap:
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, 'sec_res4')
                full_snap.assert_not_called()
                ctx = render_mock.call_args[0][2]
                self.assertEqual(ctx['teams_sorted'], [])
