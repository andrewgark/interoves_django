import json
from unittest.mock import patch

from django.test import TestCase

from games.admin import _set_ok
from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup, Team, Attempt
from games.views.new_ui import _compute_solved_task_ids, _new_results_compute


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


class UnifiedStatusAndPointsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def test_task_group_completion_uses_effective_max_points_even_without_ok_status(self):
        """
        Regression for replacements_lines: sometimes attempt.status may not be 'Ok',
        but accumulated points can still reach the effective maximum.

        We treat "solved" as result_points >= task.get_results_max_points() (same as results table).
        """
        game = Game.objects.create(id='u1', name='g', author='a', author_extra='')
        team = Team.objects.create(name='u1_team', visible_name='T')

        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=1, name='tg')
            # 3 lines, multiplier=2 => effective max = 6
            checker_data = json.dumps({'lines': [['a'], ['b'], ['c']]})
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=checker_data,
                text='',
            )

        # Create multiple submissions with points 0,2,4,6, but NEVER status Ok.
        # This simulates a historical inconsistency; solved detection must still work.
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(task=task, team=team, game=game, text='x0', status='Wrong', points=0)
            Attempt.manager.create(task=task, team=team, game=game, text='x1', status='Partial', points=2)
            Attempt.manager.create(task=task, team=team, game=game, text='x2', status='Partial', points=4)
            Attempt.manager.create(task=task, team=team, game=game, text='x3', status='Partial', points=6)

        solved_task_ids, tg_to_task_ids = _compute_solved_task_ids(
            game=game,
            task_groups=[tg],
            team=team,
            user=None,
            anon_key=None,
            mode='general',
        )
        self.assertIn(task.id, solved_task_ids)
        self.assertEqual(tg_to_task_ids[tg.id], [task.id])

    def test_admin_set_ok_uses_effective_max_for_replacements_lines(self):
        game = Game.objects.create(id='u2', name='g', author='a', author_extra='')
        team = Team.objects.create(name='u2_team', visible_name='T')

        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg2')
            GameTaskGroup.objects.create(game=game, task_group=tg, number=1, name='tg')
            checker_data = json.dumps({'lines': [['a'], ['b'], ['c']]})
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=checker_data,
                text='',
            )
            attempt = Attempt.manager.create(task=task, team=team, game=game, text='x', status='Wrong', points=0)

        _set_ok(attempt)
        attempt.refresh_from_db()
        self.assertEqual(float(task.get_results_max_points()), 6.0)
        self.assertEqual(float(attempt.points), 6.0)
        self.assertEqual(attempt.status, 'Ok')

    def test_sections_solved_is_shared_across_games_for_same_task_group(self):
        """
        In project 'sections' we want 'solved before' to be consistent even if the
        attempt was made in a different game that references the same TaskGroup.
        """
        g1 = Game.objects.create(id='u3_g1', name='g1', author='a', author_extra='', project_id='sections')
        g2 = Game.objects.create(id='u3_g2', name='g2', author='a', author_extra='', project_id='sections')
        team = Team.objects.create(name='u3_team', visible_name='T')

        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg3')
            GameTaskGroup.objects.create(game=g1, task_group=tg, number=1, name='tg')
            GameTaskGroup.objects.create(game=g2, task_group=tg, number=1, name='tg')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='default',
                points=2,
                checker_data='',
                text='',
            )

        # Solved in g1 only
        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(task=task, team=team, game=g1, text='x', status='Ok', points=2)

        # Should be considered solved on g2 section page too
        solved_task_ids, _ = _compute_solved_task_ids(
            game=g2,
            task_groups=[tg],
            team=team,
            user=None,
            anon_key=None,
            mode='general',
        )
        self.assertIn(task.id, solved_task_ids)

    def test_section_results_table_counts_attempts_from_other_game_same_task_group(self):
        """
        Results table for a section game must aggregate Attempt rows from any linked game;
        HintAttempt was never game-scoped, so without this fix teams saw hints but n_attempts=0.
        """
        g1 = Game.objects.create(id='u4_g1', name='g1', author='a', author_extra='', project_id='sections')
        g2 = Game.objects.create(id='u4_g2', name='g2', author='a', author_extra='', project_id='sections')
        team = Team.objects.create(name='u4_team', visible_name='T')

        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg4')
            GameTaskGroup.objects.create(game=g1, task_group=tg, number=1, name='tg')
            GameTaskGroup.objects.create(game=g2, task_group=tg, number=1, name='tg')
            checker = CheckerType.objects.get(pk='equals_with_possible_spaces')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='default',
                checker=checker,
                points=2,
                checker_data='',
                text='',
                answer='ok',
            )

        with patch('games.views.track.track_task_change'):
            Attempt.manager.create(task=task, team=team, game=g1, text='ok', status='Ok', points=2)

        data = _new_results_compute(g2, mode='general')
        self.assertIn(team, data['team_to_score'])
        cells = data['team_to_cells'][team]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]['n_attempts'], 1)
        self.assertGreaterEqual(float(cells[0]['result_points']), 2.0)

