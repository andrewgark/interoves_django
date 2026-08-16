from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.utils import timezone

from allauth.socialaccount.models import SocialApp

from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Registration,
    Task,
    TaskGroup,
    Team,
)


def _ensure_reference_rows():
    Project.objects.get_or_create(pk='main', defaults={})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
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
        cls.live = Game.objects.create(
            id='des_live',
            name='Идёт сейчас',
            outside_name='Идёт сейчас',
            author='Автор',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=True,
            requires_ticket=False,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            project_id='main',
        )
        cls.tg = TaskGroup.objects.create(label='live_tg')
        GameTaskGroup.objects.create(
            game=cls.live,
            task_group=cls.tg,
            number='1',
            name='Пропорции',
        )
        cls.task = Task.objects.create(
            task_group=cls.tg,
            number='1',
            text='Тестовое задание',
            answer='ОТВЕТ',
            checker_id='equals_with_possible_spaces',
            points=1,
        )

        cls.team_reg = Team.objects.create(name='team_reg_ann', visible_name='Зареганы')
        cls.team_other = Team.objects.create(name='team_other_ann', visible_name='Не зареганы')
        Registration.objects.create(game=cls.live, team=cls.team_reg)

        cls.user_reg = User.objects.create_user('user_reg_ann', 'user_reg_ann@example.com', 'pw')
        Profile.objects.create(
            user=cls.user_reg,
            first_name='A',
            last_name='Reg',
            team_on=cls.team_reg,
        )
        cls.user_other = User.objects.create_user('user_other_ann', 'user_other_ann@example.com', 'pw')
        Profile.objects.create(
            user=cls.user_other,
            first_name='B',
            last_name='Other',
            team_on=cls.team_other,
        )
        cls.user_noteam = User.objects.create_user('user_noteam_ann', 'user_noteam_ann@example.com', 'pw')
        Profile.objects.create(
            user=cls.user_noteam,
            first_name='C',
            last_name='Solo',
            team_on=None,
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

    def test_live_guest_sees_announce_not_task_list(self):
        r = self.client.get('/games/des_live/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-game-card')
        self.assertContains(r, 'Идёт сейчас')
        self.assertContains(r, 'data-login-open')
        self.assertNotContains(r, 'new-section-header--main-game')
        self.assertNotContains(r, 'Пропорции')

    def test_live_unregistered_team_sees_announce_on_game_and_task(self):
        self.assertTrue(self.client.login(username='user_other_ann', password='pw'))
        r = self.client.get('/games/des_live/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-game-card')
        self.assertContains(r, 'Зарегистрироваться')
        self.assertNotContains(r, 'new-section-header--main-game')

        r2 = self.client.get('/games/des_live/1/')
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, 'new-game-card')
        self.assertContains(r2, 'Зарегистрироваться')
        self.assertNotContains(r2, 'new-section-header--main-game')

    def test_live_no_team_still_sees_announce(self):
        self.assertTrue(self.client.login(username='user_noteam_ann', password='pw'))
        r = self.client.get('/games/des_live/1/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-game-card')
        self.assertContains(r, 'Создать команду')
        self.assertNotContains(r, 'Тестовое задание')

    def test_finished_no_team_enters_personal_mode_automatically(self):
        self.assertTrue(self.client.login(username='user_noteam_ann', password='pw'))
        self.live.end_time = timezone.now() - timedelta(minutes=1)
        self.live.save(update_fields=['end_time'])

        r = self.client.get('/games/des_live/1/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'new-game-card')
        self.assertContains(r, 'Тестовое задание')
        self.assertEqual(r.context['play_mode'], 'personal')
        self.assertFalse(r.context['lock_personal_play_mode'])

        game_page = self.client.get('/games/des_live/')
        self.assertContains(game_page, 'Вы играете лично')
        self.assertContains(game_page, 'не участвуют в командном зачёте')

    def test_live_no_team_has_no_personal_play_link_in_games_list(self):
        self.assertTrue(self.client.login(username='user_noteam_ann', password='pw'))
        r = self.client.get('/games/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Играть лично')

    def test_finished_no_team_attempt_is_saved_for_user(self):
        self.assertTrue(self.client.login(username='user_noteam_ann', password='pw'))
        self.live.end_time = timezone.now() - timedelta(minutes=1)
        self.live.save(update_fields=['end_time'])
        r = self.client.post(
            '/send_attempt/{}/'.format(self.task.pk),
            {'game_id': self.live.pk, 'text': 'ОТВЕТ'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'ok')
        attempt = Attempt.manager.get(task=self.task, user=self.user_noteam)
        self.assertIsNone(attempt.team_id)
        self.assertEqual(attempt.status, 'Ok')

    def test_finished_no_team_uses_regular_play_link(self):
        self.assertTrue(self.client.login(username='user_noteam_ann', password='pw'))
        self.live.end_time = timezone.now() - timedelta(minutes=1)
        self.live.save(update_fields=['end_time'])
        r = self.client.get('/games/')
        self.assertContains(r, 'href="/games/des_live/"', html=False)
        self.assertContains(r, '>Играть<', html=False)
        self.assertNotContains(r, 'Играть лично')

    def test_live_registered_team_sees_play_pages(self):
        self.assertTrue(self.client.login(username='user_reg_ann', password='pw'))
        r = self.client.get('/games/des_live/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-section-header--main-game')
        self.assertContains(r, 'Пропорции')
        self.assertNotContains(r, 'new-game-card')

        r2 = self.client.get('/games/des_live/1/')
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, 'Пропорции')
        self.assertNotContains(r2, 'new-game-card')

    def test_future_task_group_url_shows_announce(self):
        tg = TaskGroup.objects.create(label='future_tg')
        GameTaskGroup.objects.create(
            game=self.announced,
            task_group=tg,
            number='1',
            name='Будущее',
        )
        r = self.client.get('/games/des170/1/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'new-game-card')
        self.assertContains(r, 'Десяточка 170')
        self.assertNotContains(r, 'Будущее')
