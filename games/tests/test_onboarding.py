from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
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
from games.section_hub import onboarding_followup_context
from games.views.new_ui import _onboarding_starter_salad_url


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

    def test_start_is_direct_public_route_with_one_primary_and_two_alternatives(self):
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
        self.assertIn('Начать первую игру', body)
        self.assertEqual(body.count('new-start-primary__cta'), 1)
        self.assertIn('Самая простая', body)
        self.assertIn('Посложнее', body)
        self.assertNotIn('Хотите посложнее?', body)
        self.assertIn('Посмотреть все игры →', body)

        salad = body.index('data-onboarding-game="salad"')
        alphabet = body.index('data-onboarding-game="alphabetty"')
        ladder = body.index('data-onboarding-game="ladder"')
        self.assertLess(salad, alphabet)
        self.assertLess(alphabet, ladder)

    def test_start_has_focused_header_without_old_value_proposition(self):
        response = self.client.get('/start/')
        body = response.content.decode()

        self.assertNotContains(response, 'Каждый день — новая интеллектуальная игра')
        self.assertNotContains(response, 'Бесплатно · без регистрации')
        self.assertNotContains(response, 'Новая игра каждый день')
        self.assertNotContains(response, 'игр в архиве')
        self.assertIn('class="new-nav__focused"', body)
        self.assertIn('>Все игры</a>', body)
        self.assertNotIn('class="new-nav__sections"', body)
        self.assertNotIn('id="new-nav-drawer"', body)

    def test_configured_published_starter_salad_is_used(self):
        with override_settings(ONBOARDING_STARTER_SALAD_ID='1'):
            self.assertEqual(
                _onboarding_starter_salad_url([
                    {'id': 'salad', 'published_numbers': {'1'}},
                ]),
                '/salad/1/',
            )

    @override_settings(ONBOARDING_STARTER_SALAD_ID='999999')
    def test_unpublished_starter_salad_falls_back_to_last(self):
        body = self.client.get('/start/').content.decode()
        self.assertIn('href="/salad/last/"', body)

    def test_start_uses_existing_analytics_helper_and_game_parameter(self):
        body = self.client.get('/start/').content.decode()

        self.assertIn('static/js/onboarding.js', body)
        self.assertIn('data-onboarding-recommended="1"', body)
        self.assertIn('data-onboarding-recommended="0"', body)
        self.assertIn('data-onboarding-game="alphabetty"', body)
        self.assertNotIn("ym(", body[body.index('<div class="new-start-page">'):])

    def test_all_latest_routes_resolve_without_a_hardcoded_number(self):
        for path in ('/salad/last/', '/alphabetty/last/', '/ladder/last/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (301, 302))

    def test_daily_game_pages_include_hidden_contextual_followup(self):
        salad = onboarding_followup_context('salad')
        self.assertEqual(salad['onboarding_game'], 'salad')
        self.assertEqual(salad['onboarding_followup_primary']['game'], 'alphabetty')
        self.assertEqual(salad['onboarding_salad_archive_url'], '/salad/')
        alphabetty = onboarding_followup_context('alphabetty')
        self.assertEqual(alphabetty['onboarding_game'], 'alphabetty')
        self.assertEqual(alphabetty['onboarding_followup_primary']['game'], 'salad')

    def test_home_prioritizes_daily_games_and_explains_the_choices(self):
        body = self.client.get('/').content.decode()

        self.assertNotIn('Не знаете, с чего начать?', body)
        self.assertIn('id="hub-team-heading"', body)
        self.assertIn('>Командные игры</h2>', body)
        self.assertLess(body.index('id="hub-daily-heading"'), body.index('id="hub-team-heading"'))
        self.assertLess(body.index('id="hub-team-heading"'), body.index('id="desyatochki-heading"'))
        self.assertLess(body.index('hub-section-salad'), body.index('hub-section-alphabetty'))
        self.assertLess(body.index('hub-section-alphabetty'), body.index('hub-section-ladder'))
        self.assertNotIn('new-hub-section--recommended', body)
        self.assertIn('>Как играть</button>', body)
        self.assertIn('id="desyatochki-rules-modal"', body)

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
        self.assertContains(response, 'Впервые в Inter Oves?')
