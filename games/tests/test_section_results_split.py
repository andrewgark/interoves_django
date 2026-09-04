from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from allauth.socialaccount.models import SocialApp

from games.alphabetty_daily import ALPHABETTY_GAME_ID
from games.ladder_daily import LADDER_GAME_ID
from games.models import (
    CheckerType,
    Game,
    GameResultsSnapshot,
    GameTaskGroup,
    HTMLPage,
    Project,
    Task,
    TaskGroup,
)
from games.results_snapshot import freeze_game_results, results_attempts_scope_game
from games.views.new_ui import (
    _results_table_headers_context,
    new_section_results_page,
    new_section_task_results_page,
    new_task_group_page,
)


def _ensure_min_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
    CheckerType.objects.get_or_create(pk='replacements_lines')
    site, _ = Site.objects.get_or_create(
        id=1,
        defaults={'domain': 'testserver', 'name': 'test'},
    )
    for provider, name in (('google', 'Google'), ('vk', 'VK'), ('yandex', 'Yandex')):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': name, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class SectionResultsSplitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def _create_section_game(self, game_id='sec_res'):
        return Game.objects.create(
            id=game_id,
            name='Section',
            author='a',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )

    def _minimal_live_payload(self, game, task, *, number='1', name='One'):
        return {
            'mode': 'general',
            'game_id': game.id,
            'task_groups': [{'number': number, 'name': name, 'tasks': [{'number': '1'}]}],
            'task_ids': [task.id],
            'rows': [],
        }

    def test_non_ladder_section_results_page_uses_shared_table(self):
        game = self._create_section_game()
        request = self.factory.get('/section/sec_res/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.views.new_ui.render') as render_mock:
            new_section_results_page(request, 'sec_res')
        context = render_mock.call_args[0][2]
        self.assertEqual(context['game'].pk, game.pk)
        self.assertFalse(context['is_ladder_results'])
        self.assertEqual(context['results_variant'], 'standard')

    def test_alphabetty_results_url_resolves_to_shared_results_page(self):
        match = resolve('/alphabetty/results/')
        self.assertIs(match.func, new_section_results_page)
        self.assertEqual(match.kwargs.get('game_id'), ALPHABETTY_GAME_ID)
        self.assertEqual(
            reverse('ui_alphabetty_results'), '/alphabetty/results/'
        )
        self.assertEqual(
            reverse('new_alphabetty_results'), '/alphabetty/results/'
        )

    def test_alphabetty_task_results_route_passes_number_to_view(self):
        match = resolve('/alphabetty/8/results/')
        self.assertIs(match.func, new_section_task_results_page)
        self.assertEqual(match.kwargs, {'game_id': ALPHABETTY_GAME_ID, 'number': '8'})

        request = self.factory.get('/alphabetty/8/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.views.new_ui.render') as render_mock:
            new_section_task_results_page(request, **match.kwargs)
        self.assertEqual(render_mock.call_args[0][1], 'ui/results.html')

    def test_ladder_results_url_resolves_to_section_results_not_task_group(self):
        match = resolve('/ladder/results/')
        self.assertIs(match.func, new_section_results_page)
        self.assertEqual(match.kwargs.get('game_id'), 'ladder')
        self.assertNotIn('task_group_number', match.kwargs)
        self.assertIsNot(match.func, new_task_group_page)
        self.assertEqual(reverse('ui_section_results', kwargs={'game_id': 'ladder'}), '/ladder/results/')
        self.assertEqual(reverse('new_section_results', kwargs={'game_id': 'ladder'}), '/ladder/results/')

    def test_freeze_skips_sections(self):
        game = self._create_section_game('sec_freeze')
        obj, did = freeze_game_results(game, mode='general', overwrite=True)
        self.assertIsNone(obj)
        self.assertFalse(did)

    def test_results_attempts_scope_ladder_vs_section(self):
        section = self._create_section_game('sec_scope')
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        self.assertIsNone(results_attempts_scope_game(section, 'general'))
        self.assertIs(results_attempts_scope_game(ladder, 'general'), ladder)
        self.assertIs(results_attempts_scope_game(section, 'tournament'), section)

    def test_ladder_initial_page_skips_bulk_attempts(self):
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        request = self.factory.get('/ladder/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.models.Attempt.manager.get_bulk_game_actor_rows') as bulk_mock:
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, LADDER_GAME_ID)
                bulk_mock.assert_not_called()
                ctx = render_mock.call_args[0][2]
                self.assertEqual(ctx['teams_sorted'], [])
                self.assertTrue(ctx['progressive_results'])
                self.assertTrue(ctx['is_ladder_results'])
                self.assertTrue(ctx['section_results'])

    def test_ladder_partial_page_loads_rows(self):
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg_ladder_partial')
            GameTaskGroup.objects.create(game=ladder, task_group=tg, number='9001', name='L9001')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='x',
                text='y',
            )

        request = self.factory.get('/ladder/results/?page=1&partial=1')
        request.user = AnonymousUser()
        request.session = {}
        payload = self._minimal_live_payload(ladder, task, number='9001', name='L9001')
        with patch(
            'games.results_snapshot.build_results_snapshot_payload',
            return_value=payload,
        ) as builder:
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, LADDER_GAME_ID)
                builder.assert_called_once()
                self.assertEqual(render_mock.call_args[0][1], 'new/partials/results_rows.html')
                self.assertEqual(render_mock.call_args[0][2].get('page_size'), 50)
                self.assertTrue(render_mock.call_args[0][2].get('is_ladder_results'))

    def test_ladder_partial_pages_reuse_live_cache(self):
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg_ladder_cache')
            GameTaskGroup.objects.create(game=ladder, task_group=tg, number='9002', name='L9002')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='x',
                text='y',
            )

        payload = self._minimal_live_payload(ladder, task, number='9002', name='L9002')
        with patch(
            'games.results_snapshot.build_results_snapshot_payload',
            return_value=payload,
        ) as builder:
            with patch('games.views.new_ui.render'):
                for page in (1, 2):
                    request = self.factory.get(
                        '/ladder/results/?page={}&partial=1'.format(page)
                    )
                    request.user = AnonymousUser()
                    request.session = {}
                    new_section_results_page(request, LADDER_GAME_ID)
                self.assertEqual(builder.call_count, 1)

    def test_headers_context_without_snapshot(self):
        game = self._create_section_game('sec_res3')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg3')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='2', name='Two')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='a',
                text='b',
            )

        ctx = _results_table_headers_context(game)
        self.assertEqual(len(ctx['task_groups']), 1)
        self.assertEqual(ctx['task_groups'][0].number, '2')
        self.assertEqual(len(ctx['task_group_to_tasks']['2']), 1)

    def test_standard_results_header_shows_only_task_group_number(self):
        game = self._create_section_game('sec_short_header')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg_short_header')
            GameTaskGroup.objects.create(
                game=game,
                task_group=tg,
                number='14',
                name='Алфавитка #14',
            )
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='equals_with_possible_spaces',
                points=1,
                checker_data='a',
                text='b',
            )

        request = self.factory.get('/section/sec_short_header/results/')
        request.user = AnonymousUser()
        request.session = {}
        response = new_section_results_page(request, game.id)

        self.assertRegex(
            response.content.decode(),
            r'<th class="is-sticky-top" colspan="1">\s*14\s*</th>',
        )
        self.assertNotContains(response, '14. Алфавитка #14')

    def test_ladder_initial_with_snapshot_uses_headers_only(self):
        ladder = Game.objects.get(pk=LADDER_GAME_ID)
        GameResultsSnapshot.objects.update_or_create(
            game=ladder,
            mode='general',
            defaults={
                'payload': {
                    'task_groups': [{'number': '1', 'name': 'One', 'tasks': [{'number': '1'}]}],
                    'rows': [],
                },
            },
        )

        request = self.factory.get('/ladder/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.results_snapshot.snapshot_to_results_context') as full_snap:
            with patch('games.views.new_ui.render') as render_mock:
                new_section_results_page(request, LADDER_GAME_ID)
                full_snap.assert_not_called()
                ctx = render_mock.call_args[0][2]
                self.assertEqual(ctx['teams_sorted'], [])
                self.assertTrue(ctx['is_ladder_results'])
                self.assertEqual(len(ctx['task_groups']), 1)
