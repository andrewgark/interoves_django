from datetime import timedelta

from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import Game, HTMLPage, Project


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})


@override_settings(LANGUAGE_CODE='ru-ru')
class AnnouncedGamePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_reference_rows()
        site, _ = Site.objects.get_or_create(id=1, defaults={'domain': 'testserver', 'name': 'test'})
        for provider, name in (('google', 'Google'), ('vk', 'VK')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)

        now = timezone.now()
        cls.announced = Game.objects.create(
            id='des170',
            name='Десяточка 170',
            outside_name='Десяточка 170',
            author='Автор',
            theme='Тема теста',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=True,
            requires_ticket=True,
            start_time=now + timedelta(days=2),
            end_time=now + timedelta(days=2, hours=2),
            project_id='main',
        )
        cls.hidden = Game.objects.create(
            id='des_hidden',
            name='Скрытая',
            author='Автор',
            is_ready=False,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=2),
            project_id='main',
        )
        cls.started = Game.objects.create(
            id='des_live',
            name='Идёт сейчас',
            outside_name='Идёт сейчас',
            author='Автор',
            is_ready=True,
            is_playable=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            project_id='main',
        )

    def test_announced_game_shows_card_instead_of_404(self):
        r = self.client.get('/games/des170/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-game-card')
        self.assertContains(r, 'Десяточка 170')
        self.assertContains(r, 'Тема: Тема теста')
        # Гость: кнопка входа (регистрация/билет — после логина и команды)
        self.assertContains(r, 'data-login-open')
        self.assertContains(r, 'Войти')
        self.assertNotContains(r, 'new-section-header--main-game')

    def test_not_ready_game_still_404(self):
        r = self.client.get('/games/des_hidden/')
        self.assertEqual(r.status_code, 404)

    def test_started_game_shows_play_page(self):
        r = self.client.get('/games/des_live/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-section-header--main-game')
        self.assertContains(r, 'Идёт сейчас')
        self.assertNotContains(r, 'new-game-card')
