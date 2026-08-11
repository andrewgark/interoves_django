from unittest.mock import patch

from django.test import TestCase

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
