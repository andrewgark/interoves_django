from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from games.models import Game, HTMLPage, Profile, Project
from games.site.pages.game.visible_games import GAMES_PER_PAGE

LOCK_CHECK = 'games.club.access.archive.user_can_access_desyatka'


class ArchiveLockScopeTests(TestCase):
    """Club archive locks are checked only for the games a page renders."""

    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main')
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        now = timezone.now()
        for index in range(GAMES_PER_PAGE + 5):
            start = now - timedelta(days=index + 1)
            Game.objects.create(
                id='lockscope{}'.format(index), name='Десяточка {}'.format(index),
                author='test', project_id='main', is_ready=True,
                start_time=start, end_time=start + timedelta(hours=2),
            )
        cls.user = User.objects.create_user('lock-scope', password='secret')
        Profile.objects.create(user=cls.user, first_name='L', last_name='S')

    def setUp(self):
        self.client.force_login(self.user)

    def test_hub_does_not_check_archive_locks(self):
        with patch(LOCK_CHECK, return_value=True) as check:
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['desyatochki_card']['game'].id, 'lockscope0')
        check.assert_not_called()

    def test_games_list_checks_only_the_rendered_page(self):
        with patch(LOCK_CHECK, return_value=False) as check:
            response = self.client.get('/games/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(check.call_count, GAMES_PER_PAGE)
        self.assertTrue(all(game.is_archive_locked for game in response.context['games']))

        with patch(LOCK_CHECK, return_value=False) as check:
            response = self.client.get(
                '/games/', {'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(check.call_count, 5)
        self.assertIn('Открыть архив', response.json()['games_html'])
