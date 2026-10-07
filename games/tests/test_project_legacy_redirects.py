from datetime import timedelta

from allauth.socialaccount.models import SocialApp
from django.test import TestCase
from django.contrib.sites.models import Site
from django.utils import timezone

from games.models import Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup


class ProjectLegacyRedirectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='glowbyte', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        site, _ = Site.objects.get_or_create(
            id=1,
            defaults={'domain': 'testserver', 'name': 'test'},
        )
        for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
            app, created = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
            )
            if created:
                app.sites.add(site)
        now = timezone.now()
        game = Game.objects.create(
            id='glowbyte_des_12',
            name='Glowbyte 12',
            outside_name='Glowbyte 12',
            author='Author',
            is_ready=True,
            is_playable=True,
            is_tournament=True,
            is_registrable=False,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
            project_id='glowbyte',
        )
        task_group = TaskGroup.objects.create(label='Glowbyte round 1')
        GameTaskGroup.objects.create(
            game=game,
            task_group=task_group,
            number='1',
            name='Round 1',
        )
        Task.objects.create(task_group=task_group, number='1', text='Task text')

    def assertPermanentRedirectsTo(self, source, target):
        response = self.client.get(source)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], target)

    def test_root_game_page_redirects_to_project_game_page(self):
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/',
            '/glowbyte/games/glowbyte_des_12/',
        )

    def test_root_tournament_results_redirects_to_project_results(self):
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/tournament-results/',
            '/glowbyte/games/glowbyte_des_12/tournament-results/',
        )

    def test_legacy_tournament_results_redirects_to_project_results(self):
        self.assertPermanentRedirectsTo(
            '/tournament_results/glowbyte_des_12/',
            '/glowbyte/games/glowbyte_des_12/tournament-results/',
        )

    def test_redirect_preserves_query_string(self):
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/results/?actors=team',
            '/glowbyte/games/glowbyte_des_12/results/?actors=team',
        )

    def test_task_group_links_redirect_to_project_scope(self):
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/1/results/',
            '/glowbyte/games/glowbyte_des_12/1/results/',
        )
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/1/',
            '/glowbyte/games/glowbyte_des_12/1/',
        )

    def test_old_ui_deep_links_redirect_to_project_scope(self):
        self.assertPermanentRedirectsTo(
            '/old/games/glowbyte_des_12/',
            '/glowbyte/games/glowbyte_des_12/',
        )
        self.assertPermanentRedirectsTo(
            '/old/games/glowbyte_des_12/1',
            '/glowbyte/games/glowbyte_des_12/1/',
        )
        self.assertPermanentRedirectsTo(
            '/old/games/glowbyte_des_12/1/1?from=legacy',
            '/glowbyte/games/glowbyte_des_12/1/?from=legacy',
        )

    def test_old_ui_results_redirect_to_project_scope(self):
        self.assertPermanentRedirectsTo(
            '/old/results/glowbyte_des_12/',
            '/glowbyte/games/glowbyte_des_12/results/',
        )
        self.assertPermanentRedirectsTo(
            '/old/tournament_results/glowbyte_des_12/?actors=team',
            '/glowbyte/games/glowbyte_des_12/tournament-results/?actors=team',
        )

    def test_root_progress_live_state_and_timing_redirect_to_project_scope(self):
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/progress/',
            '/glowbyte/games/glowbyte_des_12/progress/',
        )
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/live-state/?task_ids=1',
            '/glowbyte/games/glowbyte_des_12/live-state/?task_ids=1',
        )
        self.assertPermanentRedirectsTo(
            '/games/glowbyte_des_12/1/timing/?session_id=abc',
            '/glowbyte/games/glowbyte_des_12/1/timing/?session_id=abc',
        )

    def test_project_game_page_uses_canonical_results_links(self):
        response = self.client.get('/glowbyte/games/glowbyte_des_12/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="/glowbyte/games/glowbyte_des_12/tournament-results/"',
        )
        self.assertContains(
            response,
            'data-track-url="/glowbyte/games/glowbyte_des_12/track"',
        )
        self.assertNotContains(
            response,
            'href="/games/glowbyte_des_12/tournament-results/"',
        )

    def test_project_task_group_page_uses_project_live_state_and_timing_urls(self):
        response = self.client.get('/glowbyte/games/glowbyte_des_12/1/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-track-live-state-url="/glowbyte/games/glowbyte_des_12/live-state/"',
        )
        self.assertContains(
            response,
            'data-track-url="/glowbyte/games/glowbyte_des_12/track"',
        )

    def test_daily_timing_context_uses_project_url(self):
        from games.views.daily_timing_views import daily_timing_page_context

        game = Game.objects.get(pk='glowbyte_des_12')
        placement = game.task_group_links.select_related('task_group').get(number='1')
        context = daily_timing_page_context(
            None,
            game,
            placement,
            anon_key='anon',
        )

        self.assertEqual(
            context['daily_timing_url'],
            '/glowbyte/games/glowbyte_des_12/1/timing/',
        )

    def test_project_timing_endpoint_accepts_project_id(self):
        response = self.client.get(
            '/glowbyte/games/glowbyte_des_12/1/timing/?session_id=abc',
            HTTP_X_INTEROVES_ANON='anon-project-timing',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_project_tournament_results_uses_canonical_general_results_link(self):
        response = self.client.get('/glowbyte/games/glowbyte_des_12/tournament-results/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="/glowbyte/games/glowbyte_des_12/results/"',
        )
        self.assertNotContains(response, 'href="/games/glowbyte_des_12/results/"')

    def test_telegram_result_urls_use_project_scope(self):
        from games.telegram.game_urls import game_standings_url, game_tournament_results_url

        game = Game.objects.get(pk='glowbyte_des_12')

        self.assertEqual(
            game_standings_url(game),
            'https://interoves.com/glowbyte/games/glowbyte_des_12/results/',
        )
        self.assertEqual(
            game_tournament_results_url(game),
            'https://interoves.com/glowbyte/games/glowbyte_des_12/tournament-results/',
        )

    def test_task_group_navigation_helpers_infer_project_scope(self):
        from games.tasks.navigation import (
            play_url_for_task_group,
            replay_exit_url_for_task_group,
            replay_url_for_task_group,
            results_url_for_task_group,
        )

        game = Game.objects.get(pk='glowbyte_des_12')

        self.assertEqual(
            play_url_for_task_group(game, '1'),
            '/glowbyte/games/glowbyte_des_12/1/',
        )
        self.assertEqual(
            results_url_for_task_group(game, '1'),
            '/glowbyte/games/glowbyte_des_12/1/results/',
        )
        self.assertEqual(
            replay_url_for_task_group(game, '1'),
            '/glowbyte/games/glowbyte_des_12/1/replay/',
        )
        self.assertEqual(
            replay_exit_url_for_task_group(game, '1'),
            '/glowbyte/games/glowbyte_des_12/1/replay/exit/',
        )
