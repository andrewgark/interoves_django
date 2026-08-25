from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from allauth.socialaccount.models import SocialApp

from games.models import (
    Attempt,
    Game,
    PlayerCompletedGame,
    PlayerStartedGame,
    Project,
    TaskGroup,
)


def _ensure_social_apps():
    site = Site.objects.get_current()
    for provider in ('google', 'vk'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class OnboardingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_social_apps()
        main = Project.objects.get(pk='main')
        now = timezone.now()
        Game.objects.create(
            id='onboarding_desyatochki',
            name='Тестовая Десяточка',
            outside_name='Тестовая Десяточка',
            author='Автор',
            project=main,
            start_time=now,
            end_time=now,
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            is_registrable=False,
            requires_ticket=False,
            rules=None,
            tournament_rules=None,
            general_rules=None,
        )
        sections = Project.objects.get(pk='sections')
        Game.objects.create(
            id='onboarding_internal_section',
            name='Служебный раздел',
            outside_name='Служебный раздел',
            author='Автор',
            project=sections,
            start_time=now,
            end_time=now,
            is_ready=True,
            is_playable=True,
            is_tournament=False,
            is_registrable=False,
            requires_ticket=False,
            rules=None,
            tournament_rules=None,
            general_rules=None,
        )

    def setUp(self):
        self.client = Client()

    def test_start_is_direct_public_route_with_three_stable_latest_ctas(self):
        response = self.client.get('/start/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve('/start/').url_name, 'ui_start')
        self.assertEqual(reverse('ui_start'), '/start/')
        self.assertEqual(reverse('new_start'), '/start/')

        body = response.content.decode()
        self.assertIn('<title>С чего начать · Interoves</title>', body)
        self.assertIn('name="description" content="Начните знакомство', body)
        self.assertIn('<link rel="canonical" href="https://interoves.com/start/">', body)
        self.assertIn('href="/salad/last/"', body)
        self.assertIn('href="/alphabetty/last/"', body)
        self.assertIn('href="/ladder/last/"', body)
        self.assertIn('Рекомендуем начать здесь', body)
        self.assertIn('Самая простая', body)
        self.assertIn('Посложнее', body)

        salad = body.index('data-onboarding-game="salad"')
        alphabet = body.index('data-onboarding-game="alphabet"')
        ladder = body.index('data-onboarding-game="ladder"')
        advanced = body.index('Хотите посложнее?')
        self.assertLess(salad, alphabet)
        self.assertLess(alphabet, ladder)
        self.assertLess(ladder, advanced)

    def test_start_uses_existing_analytics_helper_and_game_parameter(self):
        body = self.client.get('/start/').content.decode()

        self.assertIn("'onboarding_start_view'", body)
        self.assertIn("'onboarding_game_select'", body)
        self.assertIn('analytics.trackYandexGoalOnce(', body)
        self.assertIn("{ game: link.getAttribute('data-onboarding-game') }", body)
        self.assertIn('data-onboarding-game="alphabet"', body)
        self.assertNotIn('data-onboarding-game="alphabetty"', body)
        self.assertNotIn("ym(", body[body.index('<div class="new-start-page">'):])

    def test_all_latest_routes_resolve_without_a_hardcoded_number(self):
        for path in ('/salad/last/', '/alphabetty/last/', '/ladder/last/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (301, 302))

    def test_home_prioritizes_daily_games_and_explains_the_choices(self):
        body = self.client.get('/').content.decode()

        self.assertIn('Попробуйте одну из ежедневных игр.', body)
        self.assertIn('id="hub-team-heading"', body)
        self.assertIn('>Командные игры</h2>', body)
        self.assertLess(body.index('id="hub-daily-heading"'), body.index('id="hub-team-heading"'))
        self.assertLess(body.index('id="hub-team-heading"'), body.index('id="desyatochki-heading"'))
        self.assertLess(body.index('hub-section-salad'), body.index('hub-section-alphabetty'))
        self.assertLess(body.index('hub-section-alphabetty'), body.index('hub-section-ladder'))
        self.assertIn('new-hub-section--recommended', body)

    def test_unknown_internal_section_does_not_break_or_enter_public_navigation(self):
        for path in ('/', '/start/'):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Служебный раздел')


class FirstVisitOnboardingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_social_apps()
        cls.game = Game.objects.get(pk='salad')
        cls.task_group = TaskGroup.objects.create(label='onboarding-start-marker')
        cls.user = get_user_model().objects.create_user(
            username='onboarding-user',
            password='test-password',
        )

    def setUp(self):
        self.client = Client()

    def _started_game(self, **actor):
        return PlayerStartedGame.objects.create(
            game=self.game,
            task_group=self.task_group,
            game_kind='salad',
            game_instance_id='salad:onboarding-test',
            public_game_id='salad:1',
            **actor,
        )

    def _completed_game(self, **actor):
        return PlayerCompletedGame.objects.create(
            game=self.game,
            task_group=self.task_group,
            game_kind='salad',
            game_instance_id='salad:onboarding-completed-test',
            public_game_id='salad:1',
            result=PlayerCompletedGame.RESULT_SOLVED,
            **actor,
        )

    def test_new_anonymous_browser_gets_server_rendered_onboarding(self):
        response = self.client.get('/')

        self.assertTrue(response.context['show_first_visit_onboarding'])
        self.assertContains(response, 'Начните с Салатика')
        self.assertContains(response, 'Правила понятны за минуту')

    def test_anonymous_browser_with_game_start_does_not_get_onboarding(self):
        anon_key = 'onboarding-anon-key'
        self._started_game(anon_key=anon_key)
        self.client.cookies['interoves_anon'] = anon_key

        response = self.client.get('/')

        self.assertFalse(response.context['show_first_visit_onboarding'])
        self.assertNotContains(response, 'new-first-visit__kicker')

    def test_new_authenticated_user_gets_onboarding(self):
        self.client.force_login(self.user)

        response = self.client.get('/')

        self.assertTrue(response.context['show_first_visit_onboarding'])

    def test_authenticated_user_with_game_start_does_not_get_onboarding(self):
        self._started_game(user=self.user)
        self.client.force_login(self.user)

        response = self.client.get('/')

        self.assertFalse(response.context['show_first_visit_onboarding'])
        self.assertNotContains(response, 'new-first-visit__kicker')

    def test_authenticated_user_with_legacy_attempt_does_not_get_onboarding(self):
        Attempt.manager.create(
            user=self.user,
            text='legacy answer',
            status='Wrong',
            skip=False,
        )
        self.client.force_login(self.user)

        response = self.client.get('/')

        self.assertFalse(response.context['show_first_visit_onboarding'])

    def test_anonymous_browser_with_completion_but_no_start_is_not_new(self):
        anon_key = 'legacy-completed-anon'
        self._completed_game(anon_key=anon_key)
        self.client.cookies['interoves_anon'] = anon_key

        response = self.client.get('/')

        self.assertFalse(response.context['show_first_visit_onboarding'])

    def test_authenticated_browser_keeps_anon_history_before_optional_merge(self):
        anon_key = 'pre-signup-anon-history'
        self._started_game(anon_key=anon_key)
        self.client.cookies['interoves_anon'] = anon_key
        self.client.force_login(self.user)

        response = self.client.get('/')

        self.assertFalse(response.context['show_first_visit_onboarding'])

    def test_start_page_stays_available_after_game_start(self):
        self._started_game(user=self.user)
        self.client.force_login(self.user)

        response = self.client.get('/start/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Впервые в Inter Oves? Начните отсюда')
