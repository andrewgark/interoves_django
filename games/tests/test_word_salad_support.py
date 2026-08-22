from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from games.support.constants import SUPPORT_CONSOLE_GROUP
from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task
from games.support.services.sections import get_sections_dashboard
from games.support.services.word_salad import (
    WordSaladSupportError,
    create_word_salad,
    delete_word_salad,
    get_word_salad_detail,
    list_word_salad_rows,
    reorder_word_salads,
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

    def test_empty_dashboard_does_not_create_game_on_get(self):
        self.client.force_login(self.support_user)
        Game.objects.filter(pk='word_salad').delete()
        response = self.client.get(reverse('support:word_salad'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Game.objects.filter(pk='word_salad').exists())

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

    def test_update_rejects_invalid_puzzles_without_changing_task(self):
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        task = Task.objects.get(pk=detail['task_id'])
        original_checker_data = task.checker_data

        invalid_cases = (
            (
                'wrong grid size',
                'A B C D\nE F G H\nI J K L\nM N O',
                'ABCD',
                'ровно 16 букв',
            ),
            (
                'missing word path',
                'A B C D\nH G F E\nI J K L\nP O N M',
                'ZZZ',
                'дорожка',
            ),
            (
                'duplicate words',
                'A B C D\nH G F E\nI J K L\nP O N M',
                'ABCDEFGHIJKLMNOP\nABCDEFGHIJKLMNOP',
                'повторяться',
            ),
            (
                'removable initial cell',
                'A B C D\nH G F E\nI J K L\nP O N M',
                'ABCD',
                'можно убрать',
            ),
        )
        for label, grid_text, words_text, expected_error in invalid_cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(WordSaladSupportError, expected_error):
                    update_word_salad(
                        detail['link_id'],
                        intro='invalid',
                        grid_text=grid_text,
                        words_text=words_text,
                    )
                task.refresh_from_db()
                self.assertEqual(task.checker_data, original_checker_data)
                self.assertEqual(task.text, '')

    def test_reorder_keeps_ids_and_updates_default_titles(self):
        with patch('games.views.track.track_task_change'):
            first = create_word_salad()
            second = create_word_salad()
            third = create_word_salad()
            update_word_salad(
                second['link_id'],
                intro='',
                grid_text=second['grid_text'],
                words_text=second['words_text'],
                name='Авторское название',
            )

        rows = reorder_word_salads([
            third['link_id'],
            second['link_id'],
            first['link_id'],
        ])

        self.assertEqual(
            [row.link_id for row in rows],
            [third['link_id'], second['link_id'], first['link_id']],
        )
        self.assertEqual([row.number for row in rows], [1, 2, 3])
        self.assertEqual(rows[0].name, 'Салат #1')
        self.assertEqual(rows[1].name, 'Авторское название')
        self.assertEqual(rows[2].name, 'Салат #3')

    def test_insert_and_delete_keep_numbers_contiguous(self):
        with patch('games.views.track.track_task_change'):
            first = create_word_salad()
            second = create_word_salad()
            inserted = create_word_salad(at_number=2)

        self.assertEqual(
            [row.link_id for row in list_word_salad_rows()],
            [first['link_id'], inserted['link_id'], second['link_id']],
        )
        rows = delete_word_salad(inserted['link_id'])
        self.assertEqual([row.number for row in rows], [1, 2])
        self.assertEqual([row.link_id for row in rows], [first['link_id'], second['link_id']])
        self.assertEqual(GameTaskGroup.objects.get(pk=second['link_id']).name, 'Салат #2')

    def test_preview_task_group_renders_word_salad_grid(self):
        self.client.force_login(self.support_user)
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        response = self.client.get(detail['preview_url'])
        self.assertEqual(response.status_code, 200)
        self.assertIn('new-word-salad__cell', response.content.decode('utf-8'))
        self.assertIn('new-word-salad__word', response.content.decode('utf-8'))
        self.assertIn('data-preview-normalized=', response.content.decode('utf-8'))
        self.assertIn('js/new_word_salad.js', response.content.decode('utf-8'))

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

    def test_invalid_save_endpoint_returns_400(self):
        self.client.force_login(self.support_user)
        with patch('games.views.track.track_task_change'):
            detail = create_word_salad()
        response = self.client.post(
            reverse('support:word_salad_save', kwargs={'link_id': detail['link_id']}),
            data={
                'intro': '',
                'name': detail['name'],
                'grid_text': 'A B C D',
                'words_text': 'ABCD',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        self.assertIn('ровно 16 букв', response.json()['error'])

    def test_reorder_endpoint(self):
        self.client.force_login(self.support_user)
        with patch('games.views.track.track_task_change'):
            first = create_word_salad()
            second = create_word_salad()
        response = self.client.post(
            reverse('support:word_salad_reorder'),
            data={'order': [second['link_id'], first['link_id']]},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['link_id'] for row in response.json()['rows']],
            [second['link_id'], first['link_id']],
        )

    def test_dashboard_uses_schedule_cards_and_modal(self):
        self.client.force_login(self.support_user)
        with patch('games.views.track.track_task_change'):
            create_word_salad()
        response = self.client.get(reverse('support:word_salad'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'word-salad-bootstrap')
        self.assertContains(response, 'support-schedule-list')
        self.assertContains(response, 'new-rules-modal')
