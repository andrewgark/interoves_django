import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from games.check import CheckerFactory
from games.admin import WordSaladTaskForm
from games.models import (
    Attempt,
    ChainTaskState,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.views.attempt_views import check_attempt
from games.views.hint_views import process_send_hint_attempt
from games.word_salad import build_ui_context, mask_for_word, serialize_task_data, validate_task_data


def _setup_db():
    Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='word_salad')


def _puzzle():
    return {
        'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
        'words': ['ABCDEFGHIJKLMNOP'],
    }


def _path():
    return [0, 1, 2, 3, 7, 6, 5, 4, 8, 9, 10, 11, 15, 14, 13, 12]


class WordSaladTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='word_salad_test',
                name='Word Salad test',
                author='test',
                author_extra='',
                project_id='sections',
                is_ready=True,
            )
            cls.tg = TaskGroup.objects.create(label='word_salad_tg', points=1)
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='WS')
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='word_salad',
                checker=CheckerType.objects.get(pk='word_salad'),
                points=2,
                checker_data=json.dumps(_puzzle(), ensure_ascii=False),
                text='Тема: алфавитная дорожка',
            )
        cls.team = Team.objects.create(name='word_salad_team', visible_name='W')

    def test_validate_task_data_accepts_puzzle(self):
        grid, words = validate_task_data(self.task.checker_data, '')
        self.assertEqual(len(grid), 16)
        self.assertEqual(len(words), 1)

    def test_mask_uses_white_square_emoji(self):
        self.assertEqual(mask_for_word('AB-CD'), '⬜⬜-⬜⬜')

    def test_admin_form_serializes_word_salad_fields(self):
        form = WordSaladTaskForm(
            data={
                'number': '2',
                'task_type': 'word_salad',
                'word_salad_grid_text': 'A B C D\nH G F E\nI J K L\nP O N M',
                'word_salad_words_text': 'ABCDEFGHIJKLMNOP\nABCD',
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(
            obj.checker_data,
            serialize_task_data(
                'A B C D\nH G F E\nI J K L\nP O N M',
                'ABCDEFGHIJKLMNOP\nABCD',
            ),
        )
        self.assertEqual(obj.answer, '')

    def test_admin_form_prefills_existing_word_salad(self):
        form = WordSaladTaskForm(instance=self.task)
        self.assertIn('A B C D', form['word_salad_grid_text'].value())
        self.assertIn('ABCDEFGHIJKLMNOP', form['word_salad_words_text'].value())

    def test_hint_attempt_updates_chain_state(self):
        attempt = Attempt(
            task=self.task,
            team=self.team,
            text=json.dumps({'action': 'hint', 'word_index': 0}),
            time=timezone.now(),
            game=self.game,
        )
        check_attempt(attempt)
        row = ChainTaskState.objects.get(task=self.task, team=self.team, game=self.game, game_mode='general')
        state = json.loads(row.state)
        self.assertEqual(state['hints'], [0])
        self.assertEqual(len(state['active']), 16)
        self.assertEqual(attempt.status, 'Partial')

    def test_solve_attempt_prunes_grid_and_solves(self):
        attempt = Attempt(
            task=self.task,
            team=self.team,
            text=json.dumps({'action': 'solve', 'path': _path()}),
            time=timezone.now(),
            game=self.game,
        )
        check_attempt(attempt)
        row = ChainTaskState.objects.get(task=self.task, team=self.team, game=self.game, game_mode='general')
        state = json.loads(row.state)
        self.assertEqual(state['solved_indices'], [0])
        self.assertEqual(state['active'], [])
        self.assertEqual(attempt.status, 'Ok')

    def test_build_ui_context_preserves_empty_active(self):
        grid, words = build_ui_context(
            _puzzle()['grid'],
            _puzzle()['words'],
            {'solved_indices': [0], 'hints': [], 'active': []},
        )['grid_rows'], build_ui_context(
            _puzzle()['grid'],
            _puzzle()['words'],
            {'solved_indices': [0], 'hints': [], 'active': []},
        )['words']
        self.assertTrue(all(not cell['is_active'] for row in grid for cell in row))
        self.assertEqual(len(words), 1)

    def test_checker_factory_knows_word_salad(self):
        checker = CheckerFactory().create_checker(
            CheckerType.objects.get(pk='word_salad'),
            self.task.checker_data,
            None,
        )
        self.assertIsNotNone(checker)

    def test_hint_view_delegates_to_attempt_flow(self):
        request = RequestFactory().post(
            '/send_hint_attempt/{}/'.format(self.task.id),
            {'action': 'hint', 'word_index': 0, 'anon_key': 'anon-test'},
        )
        with patch('games.views.hint_views.get_public_task_or_404', return_value=self.task), patch(
            'games.views.attempt_views.process_send_attempt',
            return_value={'status': 'ok', 'task_id': self.task.id},
        ) as delegated:
            response = process_send_hint_attempt(request, self.task.id)
        self.assertEqual(response['status'], 'ok')
        delegated.assert_called_once()

    def test_correct_only_does_not_save_wrong_word_salad_path(self):
        response = self.client.post(
            '/send_attempt/{}/'.format(self.task.pk),
            {
                'game_id': self.game.pk,
                'anon_key': 'word-salad-auto-test',
                'action': 'solve',
                'path': json.dumps([0, 1]),
                'correct_only': '1',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertFalse(response.json()['word_salad_correct'])
        self.assertFalse(Attempt.manager.filter(task=self.task, anon_key='word-salad-auto-test').exists())

    def test_correct_only_saves_matching_word_salad_path(self):
        with patch('games.views.attempt_views.track_task_change'):
            response = self.client.post(
                '/send_attempt/{}/'.format(self.task.pk),
                {
                    'game_id': self.game.pk,
                    'anon_key': 'word-salad-auto-correct-test',
                    'action': 'solve',
                    'path': json.dumps(_path()),
                    'correct_only': '1',
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertTrue(response.json()['word_salad_correct'])
        self.assertEqual(
            Attempt.manager.filter(task=self.task, anon_key='word-salad-auto-correct-test').count(),
            1,
        )
