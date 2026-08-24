import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, SimpleTestCase, TestCase
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
from games.views.new_ui import build_task_group_task_context_dicts, new_task_group_page
from games.word_salad import (
    OVERFLOW_HINT_SQUARE,
    archive_card_meta,
    build_ui_context,
    hint_numbers_from_attempts,
    mask_for_word,
    result_square_for_hint_count,
    result_squares_for_state,
    ru_count_label,
    score_for_state,
    serialize_task_data,
    theme_from_text,
    extra_found_word,
    validate_task_data,
)


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
            cls.tg = TaskGroup.objects.create(
                label='word_salad_tg', points=1, max_attempts=None,
            )
            GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number=1, name='WS')
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='word_salad',
                checker=CheckerType.objects.get(pk='word_salad'),
                points=2,
                max_attempts=None,
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
        self.assertEqual(mask_for_word('AB-CD', reveal_count=2), 'AB-⬜⬜')

    def test_ru_count_label(self):
        self.assertEqual(ru_count_label(1, 'слово', 'слова', 'слов'), '1 слово')
        self.assertEqual(ru_count_label(2, 'слово', 'слова', 'слов'), '2 слова')
        self.assertEqual(ru_count_label(5, 'слово', 'слова', 'слов'), '5 слов')
        self.assertEqual(ru_count_label(11, 'слово', 'слова', 'слов'), '11 слов')
        self.assertEqual(ru_count_label(21, 'слово', 'слова', 'слов'), '21 слово')
        self.assertEqual(ru_count_label(22, 'слово', 'слова', 'слов'), '22 слова')

    def test_archive_card_meta_combines_theme_and_word_count(self):
        task = SimpleNamespace(
            text='Тема: Города России',
            checker_data=json.dumps({
                'grid': _puzzle()['grid'],
                'words': ['КОСТРОМА', 'САМАРА'],
            }, ensure_ascii=False),
        )
        self.assertEqual(archive_card_meta(task), 'Города России · 2 слова')
        title_only = SimpleNamespace(
            text='Салатик #3',
            checker_data=json.dumps({
                'grid': _puzzle()['grid'],
                'words': ['ABCD'],
            }),
        )
        self.assertEqual(archive_card_meta(title_only), '1 слово')

    def test_theme_from_text_strips_prefix_and_titles(self):
        self.assertEqual(theme_from_text('Тема: Города России'), 'Города России')
        self.assertEqual(theme_from_text('города России'), 'города России')
        self.assertEqual(theme_from_text('Салатик #3'), '')
        self.assertEqual(theme_from_text('Словесный салат #12'), '')
        self.assertEqual(theme_from_text(''), '')

    def test_extra_found_word_accepts_dictionary_words_outside_answers(self):
        with patch('games.alphabetty.core.is_valid_guess', return_value=True):
            self.assertEqual(extra_found_word('кот', ['лиса']), 'КОТ')
            self.assertEqual(extra_found_word('кот', ['КОТ']), '')
            self.assertEqual(extra_found_word('на', ['лиса']), '')
        with patch('games.alphabetty.core.is_valid_guess', return_value=False):
            self.assertEqual(extra_found_word('кот', ['лиса']), '')

    def test_words_are_sorted_alphabetically(self):
        context = build_ui_context(
            _puzzle()['grid'],
            ['PONM', 'ABCD', 'IJK'],
        )
        self.assertEqual(str(context['word_points']), '1')
        self.assertEqual(str(context['hint_penalty']), '0.5')
        self.assertEqual(
            [word['normalized'] for word in context['words']],
            ['ABCD', 'IJK', 'PONM'],
        )
        self.assertEqual(context['result_squares'], '')

    def test_result_squares_use_keycaps_for_hints(self):
        self.assertEqual(result_square_for_hint_count(0), '🟩')
        self.assertEqual(result_square_for_hint_count(1), '1️⃣')
        self.assertEqual(result_square_for_hint_count(10), '🔟')
        self.assertEqual(result_square_for_hint_count(11), OVERFLOW_HINT_SQUARE)
        words = ['PONM', 'ABCD', 'IJK']
        self.assertEqual(result_squares_for_state(words, {'solved_indices': [0, 2]}), '')
        squares = result_squares_for_state(
            words,
            {
                'solved_indices': [0, 1, 2],
                'hint_counts': {1: 0, 2: 3, 0: 12},
            },
        )
        self.assertEqual(squares, '🟩3️⃣*️⃣')
        context = build_ui_context(
            _puzzle()['grid'],
            words,
            {'solved_indices': [0, 1, 2], 'hint_counts': {2: 1}},
        )
        self.assertEqual(context['result_squares'], '🟩1️⃣🟩')

    def test_elapsed_label_from_first_to_last_attempt(self):
        started = timezone.now()
        context = build_ui_context(
            _puzzle()['grid'],
            ['PONM', 'ABCD', 'IJK'],
            {'solved_indices': [0, 1, 2], 'hint_counts': {2: 1}},
            attempts=[
                SimpleNamespace(time=started),
                SimpleNamespace(time=started + timedelta(seconds=226)),
            ],
        )
        self.assertEqual(context['elapsed_label'], '3м 46с')

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
        self.assertEqual(state['hint_counts'], {'0': 1})
        self.assertEqual(len(state['active']), 16)
        self.assertEqual(attempt.status, 'Partial')

    def test_second_hint_reveals_second_letter(self):
        for hint_number in (1, 2):
            attempt = Attempt(
                task=self.task,
                team=self.team,
                text=json.dumps({
                    'action': 'hint',
                    'word_index': 0,
                    'hint_number': hint_number,
                }),
                time=timezone.now(),
                game=self.game,
            )
            check_attempt(attempt)
        row = ChainTaskState.objects.get(
            task=self.task,
            team=self.team,
            game=self.game,
            game_mode='general',
        )
        state = json.loads(row.state)
        self.assertEqual(state['hint_counts'], {'0': 2})
        context = build_ui_context(_puzzle()['grid'], _puzzle()['words'], state)
        self.assertEqual(context['words'][0]['mask_html'], 'AB⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜')
        self.assertEqual(context['words'][0]['mask_chars'][:2], ['A', 'B'])
        self.assertEqual(context['words'][0]['mask_chars'][2], '⬜')
        self.assertEqual(context['words'][0]['next_hint_number'], 3)
        self.assertEqual(score_for_state(state), 0)
        attempts = list(Attempt.manager.filter(task=self.task, team=self.team).order_by('time', 'pk'))
        self.assertEqual(hint_numbers_from_attempts(attempts), ['1.1', '1.2'])

    def test_hint_endpoint_accepts_consecutive_letters(self):
        anon_key = 'word-salad-hints-test'
        for hint_number in (1, 2):
            with patch('games.views.attempt_views.track_actor_task_change'):
                response = self.client.post(
                    '/send_hint_attempt/{}/'.format(self.task.pk),
                    {
                        'game_id': self.game.pk,
                        'anon_key': anon_key,
                        'action': 'hint',
                        'word_index': 0,
                        'hint_number': hint_number,
                    },
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'ok')
        state = json.loads(ChainTaskState.objects.get(
            task=self.task,
            anon_key=anon_key,
            game=self.game,
            game_mode='general',
        ).state)
        self.assertEqual(state['hint_counts'], {'0': 2})
        html = response.json()['update_task_html_new'][str(self.task.pk)]
        self.assertIn('title="Узнать 3 букву"', html)
        self.assertIn('ph ph-lightbulb', html)
        self.assertIn('new-word-salad__glyph">A</span>', html)
        self.assertIn('new-word-salad__glyph">B</span>', html)
        self.assertIn('new-word-salad__glyph is-blank', html)
        self.assertNotIn('new-word-salad__glyph">C</span>', html)

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
        self.assertEqual(attempt.points, 1)

    def test_one_hint_costs_half_a_point_after_solve(self):
        hint = Attempt(
            task=self.task,
            team=self.team,
            text=json.dumps({'action': 'hint', 'word_index': 0, 'hint_number': 1}),
            time=timezone.now(),
            game=self.game,
        )
        check_attempt(hint)
        solve = Attempt(
            task=self.task,
            team=self.team,
            text=json.dumps({'action': 'solve', 'path': _path()}),
            time=timezone.now(),
            game=self.game,
        )
        check_attempt(solve)
        info = Attempt.manager.get_attempts_info(self.team, self.task, game=self.game)
        self.assertEqual(solve.points, 0.5)
        self.assertEqual(info.get_result_points(), 0.5)
        self.assertEqual(info.get_hint_numbers(), ['1.1'])
        from games.views.new_ui import _new_results_compute
        results = _new_results_compute(self.game, mode='general')
        cell = results['team_to_cells'][self.team][0]
        self.assertEqual(cell['result_points'], 0.5)
        self.assertEqual(cell['hint_numbers'], ['1.1'])

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
        self.assertFalse(response.json().get('word_salad_extra'))
        self.assertFalse(Attempt.manager.filter(task=self.task, anon_key='word-salad-auto-test').exists())

    def test_correct_only_reports_dictionary_extra_without_saving(self):
        with patch('games.alphabetty.core.is_valid_guess', return_value=True):
            response = self.client.post(
                '/send_attempt/{}/'.format(self.task.pk),
                {
                    'game_id': self.game.pk,
                    'anon_key': 'word-salad-extra-test',
                    'action': 'solve',
                    'path': json.dumps([0, 1, 2]),
                    'correct_only': '1',
                },
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertFalse(payload['word_salad_correct'])
        self.assertEqual(payload['word_salad_extra'], 'ABC')
        self.assertFalse(Attempt.manager.filter(task=self.task, anon_key='word-salad-extra-test').exists())

    def test_correct_only_saves_matching_word_salad_path(self):
        with patch('games.views.attempt_views.track_actor_task_change'):
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
        self.assertFalse(response.json().get('word_salad_extra'))
        self.assertEqual(
            Attempt.manager.filter(task=self.task, anon_key='word-salad-auto-correct-test').count(),
            1,
        )
        context = build_task_group_task_context_dicts(
            self.game,
            self.tg,
            [self.task],
            None,
            None,
            'word-salad-auto-correct-test',
            'general',
        )
        rendered_state = context['word_salad_data'][self.task.pk]
        self.assertTrue(rendered_state['is_complete'])
        self.assertEqual(rendered_state['active'], [])
        self.assertEqual(rendered_state['result_squares'], '🟩')
        self.assertEqual(rendered_state['elapsed_label'], '0с')
        html = response.json()['update_task_html_new'][str(self.task.pk)]
        self.assertEqual(html.count('data-word-salad-letter></span>'), 16)
        self.assertNotIn('new-word-salad__hint-btn', html)
        self.assertIn('data-word-salad-solved>Решено!</div>', html)
        self.assertIn('data-word-salad-extras', html)

    def test_tournament_submit_without_max_attempts_does_not_500(self):
        original_start = self.game.start_time
        original_end = self.game.end_time
        self.game.start_time = timezone.now() - timedelta(hours=1)
        self.game.end_time = timezone.now() + timedelta(days=1)
        self.game.save(update_fields=['start_time', 'end_time'])
        self.assertEqual(self.game.get_current_mode(), 'tournament')
        try:
            with patch('games.views.attempt_views.track_actor_task_change'):
                solve = self.client.post(
                    '/send_attempt/{}/'.format(self.task.pk),
                    {
                        'game_id': self.game.pk,
                        'anon_key': 'word-salad-tournament-test',
                        'action': 'solve',
                        'path': json.dumps(_path()),
                        'correct_only': '1',
                    },
                )
                hint = self.client.post(
                    '/send_hint_attempt/{}/'.format(self.task.pk),
                    {
                        'game_id': self.game.pk,
                        'anon_key': 'word-salad-tournament-hint',
                        'action': 'hint',
                        'word_index': '0',
                    },
                )
        finally:
            self.game.start_time = original_start
            self.game.end_time = original_end
        self.assertEqual(solve.status_code, 200)
        self.assertEqual(solve.json()['status'], 'ok')
        self.assertTrue(solve.json()['word_salad_correct'])
        self.assertEqual(hint.status_code, 200)
        self.assertEqual(hint.json()['status'], 'ok')

    def test_public_task_group_renders_grid_and_words(self):
        request = RequestFactory().get(
            '/games/{}/1/?anon=word-salad-render-test'.format(self.game.pk),
        )
        request.user = AnonymousUser()
        request.session = {'play_mode_sections': 'personal'}
        with patch('games.views.new_ui.render', side_effect=lambda _request, _template, context: context):
            context = new_task_group_page(request, self.game.pk, '1')
        self.assertIn(self.task.pk, context['word_salad_data'])
        self.assertEqual(len(context['word_salad_data'][self.task.pk]['grid_rows']), 4)
        self.assertEqual(len(context['word_salad_data'][self.task.pk]['words']), 1)
        self.assertEqual(context['task_ui_by_task_id'][self.task.pk]['base_max'], 1)
        self.assertEqual(context['task_group_pager_label'], 'Word Salad test')

    def test_section_games_use_task_type_name_in_pager(self):
        from games.views.new_ui import _task_group_page_nav_context

        for game_id, label in (
            ('replacements', 'Замены'),
            ('walls', 'Стены'),
            ('palindromes', 'Палиндромы'),
        ):
            with self.subTest(game_id=game_id):
                game = Game(
                    id=game_id,
                    name=label,
                    project_id='sections',
                )
                context = _task_group_page_nav_context(game)
                self.assertEqual(context['task_group_pager_label'], label)
                self.assertEqual(context['task_group_results_label'], 'Результаты')
                self.assertNotIn('Набор', ' '.join(map(str, context.values())))

    def test_word_salad_game_uses_named_pager(self):
        old_id = self.game.id
        self.game.id = 'salad'
        try:
            from games.views.new_ui import _task_group_page_nav_context
            context = _task_group_page_nav_context(self.game)
        finally:
            self.game.id = old_id
        self.assertEqual(context['task_group_pager_label'], 'Салатик')

    def test_admin_changelist_renders_word_salad_attempts(self):
        Attempt.manager.create(
            team=self.team,
            task=self.task,
            game=self.game,
            text=json.dumps({'action': 'solve', 'path': _path()}),
            status='Ok',
        )
        Attempt.manager.create(
            team=self.team,
            task=self.task,
            game=self.game,
            text=json.dumps({'action': 'hint', 'word_index': 0}),
            status='Wrong',
        )
        User.objects.create_superuser('ws_admin', 'ws_admin@example.com', 'secret')
        self.client.force_login(User.objects.get(username='ws_admin'))
        response = self.client.get('/admin/games/attempt/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ABCDEFGHIJKLMNOP')
        self.assertContains(response, 'Подсказка слова 1')


class AttemptPrettyTextTests(SimpleTestCase):
    def _attempt(self, task_type, text, **task_kwargs):
        return Attempt(
            task=Task(task_type=task_type, **task_kwargs),
            text=text,
            status='Wrong',
        )

    def test_word_salad_solve_shows_letters_from_path(self):
        attempt = self._attempt(
            'word_salad',
            json.dumps({'action': 'solve', 'path': [0, 1, 2, 3]}),
            checker_data=json.dumps(_puzzle(), ensure_ascii=False),
        )
        self.assertEqual(attempt.get_pretty_text(), 'ABCD')

    def test_word_salad_hint_shows_word_index(self):
        attempt = self._attempt(
            'word_salad',
            json.dumps({'action': 'hint', 'word_index': 0}),
        )
        self.assertEqual(attempt.get_pretty_text(), 'Подсказка слова 1')

    def test_unknown_task_type_returns_raw_text(self):
        attempt = self._attempt('not_a_real_type', 'hello')
        self.assertEqual(attempt.get_pretty_text(), 'hello')
