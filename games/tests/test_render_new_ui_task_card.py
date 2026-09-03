"""Regression: AJAX partial for new UI must render wall tiles with images/audio."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from games.models import (
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Image,
    Project,
    Task,
    TaskGroup,
)
from games.views.render_task import render_new_ui_task_card_html
from games.views.new_ui import _task_ui_descriptor


def _setup_db():
    Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    for checker_id in ('wall', 'equals_with_possible_spaces', 'replacements_lines', 'raddle'):
        CheckerType.objects.get_or_create(pk=checker_id)


class RenderNewUiTaskCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        CheckerType.objects.get_or_create(pk='word_salad')
        with patch('games.views.track.track_task_change'):
            cls.game = Game.objects.create(
                id='render_wall_img_game',
                name='render wall img',
                author='test',
                author_extra='',
                project_id='sections',
            )
            cls.tg = TaskGroup.objects.create(label='render_wall_img_tg', points=1)
            GameTaskGroup.objects.create(
                game=cls.game, task_group=cls.tg, number=1, name='tg',
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='wall',
                checker=CheckerType.objects.get(pk='wall'),
                text=json.dumps({
                    'words': [
                        'IMAGE_ID=wall_img_1',
                        'IMAGE_ID=wall_img_2',
                        'word3',
                        'word4',
                    ],
                    'n_cat': 2,
                    'n_words': 2,
                }),
                checker_data=json.dumps({
                    'answers': [
                        {'words': ['IMAGE_ID=wall_img_1', 'IMAGE_ID=wall_img_2'], 'checker': 'cat1'},
                        {'words': ['word3', 'word4'], 'checker': 'cat2'},
                    ],
                    'points_words': 1,
                    'points_explanation': 1,
                    'points_bonus': 1,
                }),
            )
            cls.word_salad_task = Task.objects.create(
                task_group=cls.tg,
                number='2',
                task_type='word_salad',
                checker=CheckerType.objects.get(pk='word_salad'),
                points=1,
                checker_data=json.dumps({
                    'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
                    'words': ['ABCDEFGHIJKLMNOP'],
                }, ensure_ascii=False),
                text='Тема: алфавит',
            )
        Image.objects.get_or_create(
            id='wall_img_1',
            defaults={'image': SimpleUploadedFile('wall1.png', b'png', content_type='image/png')},
        )
        Image.objects.get_or_create(
            id='wall_img_2',
            defaults={'image': SimpleUploadedFile('wall2.png', b'png', content_type='image/png')},
        )

    def test_render_wall_with_image_tiles(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_new_ui_task_card_html(
            request, self.task, None, 'general', anon_key='anon_test', game=self.game,
        )
        self.assertIsNotNone(html)
        self.assertIn('wall-tile-image', html)

    def test_task_ui_descriptor_keeps_type_specific_renderers(self):
        default = Task(task_type='default', points=1)
        raddle = Task(task_type='raddle', points=1)
        replacements = Task(task_type='replacements_lines', points=1)
        word_salad = Task(task_type='word_salad', points=1)

        self.assertEqual(
            _task_ui_descriptor(default)['body_template'],
            'new/task-content/task-default.html',
        )
        self.assertEqual(
            _task_ui_descriptor(raddle, rd={'max_points_total': 4})['base_max'],
            4,
        )
        self.assertEqual(
            _task_ui_descriptor(replacements, rld={'max_points_total': 3, 'n_lines': 1})['body_template'],
            'task-content/task-replacements-lines.html',
        )
        self.assertEqual(
            _task_ui_descriptor(word_salad, ws={'words': []})['body_template'],
            'task-content/task-word-salad.html',
        )
        raddle_ui = _task_ui_descriptor(raddle, rd={'max_points_total': 4})
        self.assertTrue(raddle_ui['show_attempts'])
        self.assertFalse(raddle_ui['show_answer'])
        self.assertFalse(_task_ui_descriptor(default)['body_wrapper'])
        self.assertFalse(_task_ui_descriptor(word_salad, ws={'words': []})['show_attempts'])
        self.assertFalse(_task_ui_descriptor(word_salad, ws={'words': []})['show_answer'])
        self.assertEqual(
            _task_ui_descriptor(raddle)['body_error'],
            'Не получилось показать это задание. Обновите страницу или напишите о проблеме.',
        )
        self.assertEqual(
            _task_ui_descriptor(word_salad)['body_error'],
            'Не получилось показать это задание. Обновите страницу или напишите о проблеме.',
        )
        self.assertEqual(
            _task_ui_descriptor(self.task, wall_meta={'total': 6, 'title': 'wall'})['max_points_title'],
            'wall',
        )

    def test_default_and_proportions_keep_single_text_container(self):
        for task_type, task_number in (('default', '2'), ('proportions', '3')):
            task = Task.objects.create(
                task_group=self.tg,
                number=task_number,
                task_type=task_type,
                checker=CheckerType.objects.get(pk='equals_with_possible_spaces'),
                points=1,
                text='task body',
                answer='answer',
            )
            request = RequestFactory().get('/')
            request.user = AnonymousUser()
            html = render_new_ui_task_card_html(
                request, task, None, 'general', anon_key='anon_test', game=self.game,
            )
            self.assertEqual(html.count('new-taskcard__text'), 1, task_type)

    def test_invalid_special_task_renders_explicit_error(self):
        for task_number, task_type, expected in (
            ('4', 'raddle', 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'),
            ('5', 'replacements_lines', 'Не получилось показать это задание. Обновите страницу или напишите о проблеме.'),
        ):
            task = Task.objects.create(
                task_group=self.tg,
                number=task_number,
                task_type=task_type,
                checker=CheckerType.objects.get(pk=task_type),
                points=1,
                text='',
                checker_data='',
            )
            request = RequestFactory().get('/')
            request.user = AnonymousUser()
            html = render_new_ui_task_card_html(
                request, task, None, 'general', anon_key='anon_test', game=self.game,
            )
            self.assertIn(expected, html)

    def test_raddle_result_share_is_limited_to_ladder_game(self):
        request = RequestFactory().get('/')
        rd = {
            'ui': {
                'is_complete': True,
                'result_squares': '🟩',
                'elapsed_label': '3м 46с',
                'rows': [],
            },
        }
        task = SimpleNamespace(
            id=123,
            tags={},
            text='',
            task_type='raddle',
            get_player_hints=[],
        )
        context = {
            'request': request,
            'task': task,
            'rd': rd,
            'game': SimpleNamespace(id='des171_test', name='Test', outside_name=''),
            'tg_number': '3',
            'tg_name': 'Лесенки',
            'share_host': 'interoves.com',
        }
        html = render_to_string('task-content/task-raddle.html', context)
        self.assertNotIn('new-raddle-result', html)

        context['game'] = SimpleNamespace(id='ladder', name='Лесенка', outside_name='')
        html = render_to_string('task-content/task-raddle.html', context)
        self.assertIn('new-raddle-result', html)
        self.assertIn('data-share-copy-text', html)
        self.assertIn('data-share-copy-image', html)
        self.assertIn('data-share-native', html)
        self.assertIn('⏱️ 3м 46с', html)
        self.assertIn('🔗 interoves.com/ladder/3', html)

    def test_word_salad_result_share_is_limited_to_salad_game(self):
        request = RequestFactory().get('/')
        ws = {
            'is_complete': True,
            'result_squares': '🟩1️⃣🟩',
            'elapsed_label': '3м 46с',
            'grid_rows': [],
            'words': [],
        }
        task = SimpleNamespace(id=23, text='', task_type='word_salad')
        context = {
            'request': request,
            'task': task,
            'ws': ws,
            'game': SimpleNamespace(id='word_salad_test', name='Салатик', outside_name='Салатик'),
            'tg_number': '23',
            'tg_name': 'Салатик #23',
            'share_host': 'interoves.com',
        }
        html = render_to_string('task-content/task-word-salad.html', context)
        self.assertNotIn('new-raddle-result', html)

        context['game'] = SimpleNamespace(id='salad', name='Салатик', outside_name='Салатик')
        html = render_to_string('task-content/task-word-salad.html', context)
        self.assertIn('new-raddle-result', html)
        self.assertIn('data-share-native', html)
        self.assertIn('🥗 Салатик #23', html)
        self.assertIn('🟩1️⃣🟩', html)
        self.assertIn('⏱️ 3м 46с', html)
        self.assertIn('🔗 interoves.com/salad/23', html)

    def test_render_word_salad_includes_grid_and_words(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        html = render_new_ui_task_card_html(
            request, self.word_salad_task, None, 'general', anon_key='anon_test', game=self.game,
        )
        self.assertIsNotNone(html)
        self.assertIn('new-word-salad__cell', html)
        self.assertIn('new-word-salad__links', html)
        self.assertIn('new-word-salad__checking-mark', html)
        self.assertIn('title="Проверяем…"', html)
        self.assertIn('new-word-salad__word', html)
        self.assertIn('Ответы (по алфавиту)', html)
        self.assertIn('Находки не по теме', html)
        self.assertNotIn('Найденные ответы', html)
        self.assertNotIn('>Ответы<', html)
        self.assertNotIn('Ещё слова', html)
        self.assertIn('title="Узнать 1 букву"', html)
        self.assertIn('ph ph-lightbulb', html)
        self.assertIn('new-word-salad__glyph', html)
        self.assertIn('new-word-salad__reset-key', html)
        self.assertIn('>Esc<', html)
        self.assertIn('Сбросить выделение (Esc)', html)
        self.assertIn('>Сбросить</span>', html)
        self.assertIn('new-word-salad__theme-label', html)
        self.assertIn('Тема:', html)
        self.assertIn('алфавит', html)
        self.assertNotIn('Тема: Тема:', html)
        self.assertNotIn('>Слова<', html)
        self.assertNotIn(' шт.', html)
        self.assertNotIn('Готовое слово проверится автоматически', html)
