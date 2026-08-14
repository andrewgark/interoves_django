import json
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase

from games.check import GridShadingChecker, GridWallChecker
from games.grid_puzzle import (
    GridPuzzleDataError,
    canonicalize_shading,
    canonicalize_walls,
    parse_grid_puzzle_attempt,
    parse_grid_puzzle_data,
    parse_grid_shading_attempt,
    public_grid_puzzle_context,
    validate_grid_checker_data,
)
from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)
from games.views.new_ui import _task_ui_descriptor
from games.views.render_task import render_new_ui_task_card_html


PUZZLE = {
    'version': 1,
    'rows': 3,
    'cols': 4,
    'marks': [
        {'row': 0, 'col': 1, 'value': 'O'},
        {'row': 2, 'col': 3, 'value': 'X'},
    ],
    'solution_walls': ['v:0:2', 'h:1:0', 'h:2:3'],
}

SHADING_PUZZLE = {
    'version': 1,
    'rows': 2,
    'cols': 3,
    'marks': [
        {'row': 0, 'col': 0, 'value': 'arrow-up'},
        {'row': 0, 'col': 1, 'value': 'arrow-down'},
        {'row': 0, 'col': 2, 'value': 'arrow-left'},
        {'row': 1, 'col': 0, 'value': 'arrow-right'},
        {'row': 1, 'col': 1, 'value': 'star'},
    ],
    'can_set_walls': False,
    'can_set_path': False,
    'can_set_shading': True,
    'solution_shading': ['BGG', 'GBB'],
}


def _setup_db():
    Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='grid-wall-checker')
    CheckerType.objects.get_or_create(pk='grid-shading-checker')
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')


class GridPuzzleParserTests(TestCase):
    def test_parser_canonicalizes_solution(self):
        parsed = parse_grid_puzzle_data(json.dumps(PUZZLE))
        self.assertEqual(parsed['solution_walls'], ['h:1:0', 'h:2:3', 'v:0:2'])
        self.assertEqual(parsed['marks'][0]['value'], 'O')
        self.assertTrue(parsed['can_set_walls'])
        self.assertTrue(parsed['can_set_path'])
        self.assertFalse(parsed['can_set_shading'])

    def test_capability_flags_accept_booleans_and_reject_other_values(self):
        parsed = parse_grid_puzzle_data(dict(
            PUZZLE, can_set_walls=False, can_set_path=False, can_set_shading=True,
        ))
        self.assertFalse(parsed['can_set_walls'])
        self.assertFalse(parsed['can_set_path'])
        self.assertTrue(parsed['can_set_shading'])
        for field in ('can_set_walls', 'can_set_path', 'can_set_shading'):
            with self.subTest(field=field), self.assertRaises(GridPuzzleDataError):
                parse_grid_puzzle_data(dict(PUZZLE, **{field: 1}))

    def test_attempt_is_order_independent(self):
        attempt = parse_grid_puzzle_attempt(
            json.dumps({'walls': ['v:0:2', 'h:2:3', 'h:1:0']}), 3, 4,
        )
        self.assertEqual(attempt['walls'], ['h:1:0', 'h:2:3', 'v:0:2'])

    def test_shading_parser_requires_a_complete_black_green_matrix(self):
        self.assertEqual(canonicalize_shading(['BGG', 'GBB'], 2, 3), ['BGG', 'GBB'])
        self.assertEqual(
            parse_grid_shading_attempt({'shading': ['BGG', 'GBB']}, 2, 3),
            {'shading': ['BGG', 'GBB']},
        )
        for shading in (['BGG'], ['BG', 'GBB'], ['BGW', 'GBB'], [['B', 'G', 'G'], 'GBB']):
            with self.subTest(shading=shading), self.assertRaises(GridPuzzleDataError):
                canonicalize_shading(shading, 2, 3)

    def test_all_supported_cell_objects_are_accepted(self):
        parsed = parse_grid_puzzle_data(SHADING_PUZZLE)
        self.assertEqual(
            [mark['value'] for mark in parsed['marks']],
            ['arrow-up', 'arrow-down', 'arrow-left', 'arrow-right', 'star'],
        )

    def test_rejects_outer_and_out_of_range_edges(self):
        for edge in ('h:0:0', 'h:3:0', 'v:0:0', 'v:0:4', 'q:1:1'):
            with self.subTest(edge=edge), self.assertRaises(GridPuzzleDataError):
                canonicalize_walls([edge], 3, 4)

    def test_rejects_duplicate_edges_and_marks(self):
        with self.assertRaises(GridPuzzleDataError):
            canonicalize_walls(['h:1:0', 'h:1:0'], 3, 4)
        data = dict(PUZZLE)
        data['marks'] = [
            {'row': 0, 'col': 0, 'value': 'O'},
            {'row': 0, 'col': 0, 'value': 'X'},
        ]
        with self.assertRaises(GridPuzzleDataError):
            parse_grid_puzzle_data(data)

    def test_attempt_rejects_walls_when_wall_tool_is_disabled(self):
        with self.assertRaises(GridPuzzleDataError):
            parse_grid_puzzle_attempt(
                json.dumps({'walls': ['h:1:0']}), 3, 4, can_set_walls=False,
            )
        self.assertEqual(
            parse_grid_puzzle_attempt(
                json.dumps({'walls': []}), 3, 4, can_set_walls=False,
            ),
            {'walls': []},
        )

    def test_rejects_unknown_fields_versions_and_large_grids(self):
        bad_values = [
            dict(PUZZLE, version=2),
            dict(PUZZLE, rows=21),
            dict(PUZZLE, surprise=True),
        ]
        for data in bad_values:
            with self.subTest(data=data), self.assertRaises(GridPuzzleDataError):
                parse_grid_puzzle_data(data)

    def test_checker_requires_exact_wall_set(self):
        checker = GridWallChecker(json.dumps(PUZZLE))
        exact = json.dumps({'walls': ['h:2:3', 'v:0:2', 'h:1:0']})
        missing = json.dumps({'walls': ['h:1:0', 'v:0:2']})
        extra = json.dumps({'walls': ['h:1:0', 'h:2:3', 'v:0:2', 'v:1:1']})
        self.assertTrue(checker.bool_check(exact))
        self.assertFalse(checker.bool_check(missing))
        self.assertFalse(checker.bool_check(extra))
        self.assertFalse(checker.bool_check('{bad json'))

    def test_wall_checker_requires_wall_editing_capability(self):
        parsed = parse_grid_puzzle_data(dict(PUZZLE, can_set_walls=False))
        with self.assertRaisesRegex(GridPuzzleDataError, 'can_set_walls'):
            validate_grid_checker_data(parsed, 'grid-wall-checker')

    def test_shading_checker_requires_exact_complete_board(self):
        checker = GridShadingChecker(json.dumps(SHADING_PUZZLE))
        self.assertTrue(checker.bool_check(json.dumps({'shading': ['BGG', 'GBB']})))
        self.assertFalse(checker.bool_check(json.dumps({'shading': ['GGG', 'GBB']})))
        self.assertFalse(checker.bool_check(json.dumps({'shading': ['BGW', 'GBB']})))
        self.assertFalse(checker.bool_check('{bad json'))

    def test_task_model_validation_uses_grid_parser(self):
        task = Task(number='1', task_type='grid-puzzle', checker_data='{}')
        with self.assertRaises(ValidationError):
            task.clean()

    def test_task_model_validation_handles_missing_checker(self):
        task = Task(
            number='1',
            task_type='grid-puzzle',
            checker=None,
            task_group=None,
            checker_data=json.dumps(PUZZLE),
        )
        with self.assertRaisesRegex(ValidationError, 'checker is required'):
            task.clean()

    def test_public_context_does_not_reveal_solution_by_default(self):
        task = Task(
            pk=5,
            task_type='grid-puzzle',
            checker=CheckerType.objects.get(pk='grid-wall-checker'),
            checker_data=json.dumps(PUZZLE),
        )
        public = public_grid_puzzle_context(task)
        self.assertNotIn('walls', public)
        self.assertTrue(public['can_set_walls'])
        self.assertTrue(public['can_set_path'])
        revealed = public_grid_puzzle_context(task, reveal_solution=True, readonly=True)
        self.assertEqual(revealed['walls'], ['h:1:0', 'h:2:3', 'v:0:2'])
        self.assertTrue(revealed['readonly'])

    def test_shading_public_context_reveals_only_in_answer_mode(self):
        task = Task(
            pk=6,
            task_type='grid-puzzle',
            checker=CheckerType.objects.get(pk='grid-shading-checker'),
            checker_data=json.dumps(SHADING_PUZZLE),
        )
        public = public_grid_puzzle_context(task)
        self.assertNotIn('shading', public)
        self.assertEqual(public['checker_id'], 'grid-shading-checker')
        self.assertTrue(public['can_set_shading'])
        revealed = public_grid_puzzle_context(task, reveal_solution=True, readonly=True)
        self.assertEqual(revealed['shading'], ['BGG', 'GBB'])


class GridPuzzleMigrationTests(TestCase):
    def test_reverse_restores_old_wall_checker_when_shading_is_unused(self):
        migration = import_module('games.migrations.0171_grid_puzzle_shading')
        migration.restore_wall_checker(apps, None)
        self.assertTrue(CheckerType.objects.filter(pk='wall-checker').exists())
        self.assertFalse(CheckerType.objects.filter(pk='grid-wall-checker').exists())
        self.assertFalse(CheckerType.objects.filter(pk='grid-shading-checker').exists())

    def test_reverse_refuses_to_orphan_shading_tasks(self):
        checker = CheckerType.objects.get(pk='grid-shading-checker')
        task_group = TaskGroup.objects.create(label='migration-shading')
        Task.objects.create(
            task_group=task_group,
            number='1',
            task_type='grid-puzzle',
            checker=checker,
            checker_data=json.dumps(SHADING_PUZZLE),
        )
        migration = import_module('games.migrations.0171_grid_puzzle_shading')
        with self.assertRaisesRegex(RuntimeError, 'grid-shading-checker is in use'):
            migration.restore_wall_checker(apps, None)
        self.assertTrue(CheckerType.objects.filter(pk='grid-shading-checker').exists())


class GridPuzzleIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='grid_puzzle_test',
                name='Grid Puzzle test',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
            )
            cls.tg = TaskGroup.objects.create(label='grid_puzzle_tg', points=1, max_attempts=5)
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='Grid')
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='grid-puzzle',
                checker=CheckerType.objects.get(pk='grid-wall-checker'),
                checker_data=json.dumps(PUZZLE),
                text='<p>Arbitrary puzzle rules.</p>',
                points=2,
            )
            cls.shading_task = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='grid-puzzle',
                checker=CheckerType.objects.get(pk='grid-shading-checker'),
                checker_data=json.dumps(SHADING_PUZZLE),
                text='<p>Shade every cell.</p>',
                points=3,
            )
        cls.anon_key = 'grid-puzzle-anon'

    def setUp(self):
        self.client = Client()
        self.client.cookies['interoves_anon'] = self.anon_key

    def post_walls(self, walls):
        return self.client.post(
            '/send_attempt/{}/'.format(self.task.pk),
            {
                'game_id': self.game.pk,
                'anon_key': self.anon_key,
                'walls': json.dumps(walls),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def post_shading(self, shading):
        return self.client.post(
            '/send_attempt/{}/'.format(self.shading_task.pk),
            {
                'game_id': self.game.pk,
                'anon_key': self.anon_key,
                'shading': json.dumps(shading),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_new_ui_renderer_does_not_leak_solution(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_new_ui_task_card_html(
            request, self.task, None, 'general', anon_key=self.anon_key, game=self.game,
        )
        self.assertIn('data-grid-puzzle', html)
        self.assertIn('Arbitrary puzzle rules.', html)
        self.assertNotIn('solution_walls', html)
        self.assertNotIn('h:1:0', html)

    def test_wrong_attempt_preserves_client_contract(self):
        response = self.post_walls(['h:1:0'])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['grid_puzzle_correct'])
        self.assertNotIn('update_task_html_new', data)
        attempt = Attempt.manager.get(task=self.task, anon_key=self.anon_key)
        self.assertEqual(json.loads(attempt.text), {'walls': ['h:1:0']})
        self.assertEqual(attempt.status, 'Wrong')

    def test_correct_wall_attempt_returns_solved_card(self):
        response = self.post_walls(['v:0:2', 'h:2:3', 'h:1:0'])
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(data['grid_puzzle_correct'])
        self.assertIn('update_task_html_new', data)
        html = data['update_task_html_new'][str(self.task.pk)]
        self.assertIn('is-readonly', html)
        self.assertIn('h:1:0', html)
        attempt = Attempt.manager.get(task=self.task, anon_key=self.anon_key)
        self.assertEqual(attempt.status, 'Ok')
        self.assertEqual(attempt.points, 2)

    def test_shading_ui_and_exact_checker_contract(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_new_ui_task_card_html(
            request, self.shading_task, None, 'general', anon_key=self.anon_key, game=self.game,
        )
        self.assertIn('data-grid-shade-mode="B"', html)
        self.assertIn('data-grid-shading-input', html)
        self.assertIn('arrow-up', html)
        self.assertIn('star', html)
        self.assertNotIn('solution_shading', html)

        incomplete = self.post_shading(['BGW', 'GBB'])
        self.assertEqual(incomplete.json()['status'], 'invalid_form')
        self.assertFalse(Attempt.manager.filter(task=self.shading_task).exists())

        wrong = self.post_shading(['GGG', 'GBB'])
        self.assertEqual(wrong.json()['status'], 'ok')
        self.assertFalse(wrong.json()['grid_puzzle_correct'])
        attempt = Attempt.manager.get(task=self.shading_task, anon_key=self.anon_key)
        self.assertEqual(json.loads(attempt.text), {'shading': ['GGG', 'GBB']})

        correct = self.post_shading(['BGG', 'GBB'])
        self.assertEqual(correct.json()['status'], 'ok')
        self.assertTrue(correct.json()['grid_puzzle_correct'])

    def test_duplicate_attempt_is_canonical_across_order(self):
        first = self.post_walls(['v:0:2', 'h:1:0'])
        self.assertEqual(first.json()['status'], 'ok')
        second = self.post_walls(['h:1:0', 'v:0:2'])
        self.assertEqual(second.json()['status'], 'duplicate')

    def test_malformed_submission_is_safe_and_not_persisted(self):
        response = self.post_walls(['h:0:0'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'invalid_form')
        self.assertFalse(Attempt.manager.filter(task=self.task, anon_key=self.anon_key).exists())

    def test_descriptor_selects_grid_template_and_invalid_data_error(self):
        ui = _task_ui_descriptor(self.task, gp={'rows': 3})
        self.assertEqual(ui['body_template'], 'new/task-content/task-grid-puzzle.html')
        self.assertTrue(ui['show_answer'])
        invalid = _task_ui_descriptor(self.task)
        self.assertIsNone(invalid['body_template'])
        self.assertIn('Grid Puzzle', invalid['body_error'])

    def test_answer_endpoint_returns_readonly_solved_grid(self):
        response = self.client.get(
            '/answer/{}/?game_id={}'.format(self.task.pk, self.game.pk),
            HTTP_X_INTEROVES_ANON=self.anon_key,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        html = response.json()['html']
        self.assertIn('is-readonly', html)
        self.assertIn('h:1:0', html)
        self.assertIn('grid-puzzle-config-answer', html)

    def test_tournament_attempt_limit_uses_existing_contract(self):
        self.task.max_attempts = 1
        with patch('games.views.track.track_task_change'):
            self.task.save(update_fields=['max_attempts'])
        with patch.object(Game, 'get_current_mode', return_value='tournament'), patch.object(
            Attempt.manager, 'filter_attempts_with_mode', side_effect=lambda attempts, *args, **kwargs: attempts,
        ):
            first = self.post_walls(['h:1:0'])
            second = self.post_walls(['h:2:3'])
        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.json()['status'], 'attempt_limit_exceeded')

    def test_disabled_tools_are_hidden_and_walls_are_rejected_server_side(self):
        config = dict(PUZZLE, can_set_walls=False, can_set_path=False)
        with patch('games.views.track.track_task_change'):
            task = Task.objects.create(
                task_group=self.tg,
                number='3',
                task_type='grid-puzzle',
                checker=CheckerType.objects.get(pk='grid-wall-checker'),
                checker_data=json.dumps(config),
                points=1,
            )
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_new_ui_task_card_html(
            request, task, None, 'general', anon_key='disabled-tools', game=self.game,
        )
        self.assertNotIn('data-grid-reset-walls', html)
        self.assertNotIn('data-grid-notes-toggle', html)
        response = self.client.post(
            '/send_attempt/{}/'.format(task.pk),
            {
                'game_id': self.game.pk,
                'anon_key': 'disabled-tools',
                'walls': json.dumps(['h:1:0']),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.json()['status'], 'invalid_form')
