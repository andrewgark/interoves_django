import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from games.models import CensorlyPoolEntry
from games.support.constants import SUPPORT_CONSOLE_GROUP


class CensorlyPoolAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            'censorly_pool_staff', 'pool@example.com', 'secret',
        )
        group, _ = Group.objects.get_or_create(name=SUPPORT_CONSOLE_GROUP)
        group.user_set.add(cls.staff)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)

    def test_pool_admin_renders_nested_taxonomy_and_lists_nested_category(self):
        CensorlyPoolEntry.objects.create(
            title='Ааа тестовая статья пула',
            topics=['Культура/Музыка/Песни'],
            object_types=['Произведение'],
            difficulty='очень сложно',
        )

        page = self.client.get(reverse('support:censorly_pool'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Пул Цензурок')

        response = self.client.get(reverse('support:censorly_pool_list'), {
            'category': 'Культура',
            'subcategory': 'Музыка/Песни',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('Культура/Музыка/Песни', payload['groups']['очень сложно'][0]['topics'])
        self.assertEqual(payload['groups']['очень сложно'][0]['title'], 'Ааа тестовая статья пула')

    @patch('games.support.views.title_content_lemmas', return_value=True)
    @patch('games.support.views.build_puzzle_payload', return_value={'title': 'Тест'})
    @patch('games.support.views.fetch_article')
    def test_add_article_persists_tags_and_difficulty(self, fetch_article, _payload, _lemmas):
        fetch_article.return_value = SimpleNamespace(
            title='Тестовая песня', extract='Текст песни', pageid=1, revid=2,
            truncated=False,
        )
        response = self.client.post(
            reverse('support:censorly_pool_save'),
            data=json.dumps({
                'replacement_title': 'Тестовая песня',
                'topics': ['Культура/Музыка/Песни'],
                'types': ['Произведение'],
                'difficulty': 'очень сложно',
                'enabled': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        entry = CensorlyPoolEntry.objects.get(title='Тестовая песня')
        self.assertEqual(entry.topics, ['Культура/Музыка/Песни'])
        self.assertEqual(entry.object_types, ['Произведение'])
        self.assertEqual(entry.difficulty, 'очень сложно')
        self.assertTrue(entry.enabled)

