from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from games.support.constants import SUPPORT_CONSOLE_GROUP
from games.models import CheckerType, HTMLPage, Project
from games.support.services.sections import get_sections_dashboard
from games.support.services.word_salad import (
    WordSaladSupportError,
    create_word_salad,
    delete_word_salad,
    get_word_salad_detail,
    update_word_salad,
)


def _setup_db():
    Project.objects.get_or_create(pk='sections', defaults={'name': 'sections'})
    CheckerType.objects.get_or_create(pk='word_salad')
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


class WordSaladSupportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_db()
        group = Group.objects.create(name=SUPPORT_CONSOLE_GROUP)
        user = User.objects.create_user(username='support-user', password='x')
        user.groups.add(group)
        cls.support_user = user

    def test_sections_dashboard_includes_word_salad(self):
        rows = get_sections_dashboard()
        self.assertTrue(any(row.game_id == 'word_salad' for row in rows))

    def test_create_update_delete_word_salad(self):
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
            self.assertEqual(detail['words_count'], 1)
            updated = update_word_salad(
                detail['link_id'],
                intro='Тема: реки',
                grid_text='A B C D\nH G F E\nI J K L\nP O N M',
                words_text='ABCDEFGHIJKLMNOP',
                name='Тестовый салат',
            )
            self.assertEqual(updated['intro'], 'Тема: реки')
            self.assertEqual(updated['name'], 'Тестовый салат')
            delete_word_salad(detail['link_id'])
        with self.assertRaises(WordSaladSupportError):
            get_word_salad_detail(detail['link_id'])

    def test_preview_task_group_renders_word_salad_grid(self):
        self.client.force_login(self.support_user)
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        response = self.client.get(detail['preview_url'])
        self.assertEqual(response.status_code, 200)
        self.assertIn('new-word-salad__cell', response.content.decode('utf-8'))
        self.assertIn('new-word-salad__word', response.content.decode('utf-8'))

    def test_create_word_salad_auto_creates_checker_type(self):
        CheckerType.objects.filter(pk='word_salad').delete()
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        self.assertTrue(CheckerType.objects.filter(pk='word_salad').exists())
        self.assertEqual(detail['words_count'], 1)

    def test_create_endpoint_returns_json_error(self):
        self.client.force_login(self.support_user)
        with patch('games.support.views.create_word_salad', side_effect=WordSaladSupportError('boom')):
            response = self.client.post(reverse('support:word_salad_create'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertEqual(response.json()['error'], 'boom')
