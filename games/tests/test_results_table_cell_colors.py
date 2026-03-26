from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from games.models import Attempt, CheckerType, Game, HTMLPage, Project, Task, TaskGroup, Team
from games.views.new_ui import _new_results_compute
from games.results_snapshot import build_results_snapshot_payload, snapshot_to_results_context


def _ensure_checker_and_project():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


class ResultsTableCellColorsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_checker_and_project()
        cls.game = Game.objects.create(
            id='results_color_test_game',
            name='g',
            author='a',
            author_extra='',
        )
        cls.tg = TaskGroup.objects.create(game=cls.game, name='tg', number=1)
        with patch('games.views.track.track_task_change'):
            cls.task1 = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='default',
                points=10,
                checker_data='',
                text='',
            )
            cls.task2 = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='default',
                points=10,
                checker_data='',
                text='',
            )

        cls.team_max = Team.objects.create(name='team_max', visible_name='Max')
        cls.team_pending = Team.objects.create(name='team_pending', visible_name='Pending')
        cls.team_partial = Team.objects.create(name='team_partial', visible_name='Partial')
        cls.team_zero = Team.objects.create(name='team_zero', visible_name='Zero')

        now = timezone.now()
        with patch('games.views.track.track_task_change'):
            # Max points -> green
            Attempt.manager.create(
                text='x',
                status='Ok',
                points=10,
                time=now,
                task=cls.task1,
                team=cls.team_max,
            )
            # Non-max + Pending exists -> yellow
            Attempt.manager.create(
                text='x',
                status='Pending',
                points=5,
                time=now,
                task=cls.task1,
                team=cls.team_pending,
            )
            # Non-max + non-zero + no Pending -> blue (old partial shade)
            Attempt.manager.create(
                text='x',
                status='Partial',
                points=5,
                time=now,
                task=cls.task1,
                team=cls.team_partial,
            )
            # Has at least one attempt + result zero -> red
            Attempt.manager.create(
                text='x',
                status='Wrong',
                points=0,
                time=now,
                task=cls.task1,
                team=cls.team_zero,
            )
            # Note: no attempts for task2 for any team -> empty cells for column 2.

    def test_new_ui_compute_cell_classes(self):
        data = _new_results_compute(self.game, mode='general')
        t2_idx = 1  # second task

        self.assertEqual(data['team_to_cells'][self.team_max][0]['cls'], 'cell-full')
        self.assertEqual(data['team_to_cells'][self.team_pending][0]['cls'], 'cell-partial')
        self.assertEqual(data['team_to_cells'][self.team_partial][0]['cls'], 'cell-partial')
        self.assertEqual(data['team_to_cells'][self.team_zero][0]['cls'], 'cell-zero')

        self.assertEqual(data['team_to_cells'][self.team_max][t2_idx]['cls'], '')
        self.assertEqual(data['team_to_cells'][self.team_pending][t2_idx]['cls'], '')
        self.assertEqual(data['team_to_cells'][self.team_partial][t2_idx]['cls'], '')
        self.assertEqual(data['team_to_cells'][self.team_zero][t2_idx]['cls'], '')

    def test_snapshot_payload_and_context_cell_classes(self):
        payload = build_results_snapshot_payload(self.game, mode='general')
        ctx = snapshot_to_results_context(self.game, payload)

        t2_idx = 1
        self.assertEqual(ctx['team_to_cells'][self.team_max][0]['cls'], 'cell-full')
        self.assertEqual(ctx['team_to_cells'][self.team_pending][0]['cls'], 'cell-partial')
        self.assertEqual(ctx['team_to_cells'][self.team_partial][0]['cls'], 'cell-partial')
        self.assertEqual(ctx['team_to_cells'][self.team_zero][0]['cls'], 'cell-zero')

        self.assertEqual(ctx['team_to_cells'][self.team_max][t2_idx]['cls'], '')
        self.assertEqual(ctx['team_to_cells'][self.team_pending][t2_idx]['cls'], '')
        self.assertEqual(ctx['team_to_cells'][self.team_partial][t2_idx]['cls'], '')
        self.assertEqual(ctx['team_to_cells'][self.team_zero][t2_idx]['cls'], '')

    def test_old_results_page_context_cell_classes(self):
        client = Client()
        resp = client.get(f'/old/results/{self.game.id}/')
        self.assertEqual(resp.status_code, 200)

        t2_idx = 1
        team_to_cells = resp.context['team_to_cells']

        self.assertEqual(team_to_cells[self.team_max][0]['cls'], 'cell-ok')
        self.assertEqual(team_to_cells[self.team_pending][0]['cls'], 'cell-pending')
        self.assertEqual(team_to_cells[self.team_partial][0]['cls'], 'cell-partial')
        self.assertEqual(team_to_cells[self.team_zero][0]['cls'], 'cell-wrong')

        self.assertEqual(team_to_cells[self.team_max][t2_idx]['cls'], '')
        self.assertEqual(team_to_cells[self.team_pending][t2_idx]['cls'], '')
        self.assertEqual(team_to_cells[self.team_partial][t2_idx]['cls'], '')
        self.assertEqual(team_to_cells[self.team_zero][t2_idx]['cls'], '')

