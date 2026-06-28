"""Regression: AJAX partial for new UI must render wall tiles with images/audio."""
import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
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


def _setup_db():
    Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='wall')


class RenderNewUiTaskCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
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
