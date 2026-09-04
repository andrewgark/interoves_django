import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup, Attempt
from games.views.new_ui import (
    _game_page_progress_context,
    _initial_task_group_progress,
    _task_group_progress_payload,
    _task_group_rows_skeleton,
    _game_task_group_links,
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
    CheckerType.objects.get_or_create(pk='raddle')
    CheckerType.objects.get_or_create(pk='word_salad')


def _ensure_login_modal_deps():
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    site = Site.objects.get_current()
    for provider in ('google', 'vk', 'yandex'):
        app, _ = SocialApp.objects.get_or_create(
            provider=provider,
            defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
        )
        app.sites.add(site)


class GameTaskGroupProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_min_fixtures()

    def setUp(self):
        self.client = Client()

    def _create_section_game(self, game_id):
        return Game.objects.create(
            id=game_id,
            name='Section',
            author='a',
            author_extra='',
            project_id='sections',
            is_ready=True,
        )

    def test_section_hub_builds_skeleton_and_progress_url(self):
        game = self._create_section_game('sec_prog')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=json.dumps({'lines': [['a']]}),
                text='',
            )

        factory = RequestFactory()
        request = factory.get('/section/sec_prog/')
        request.user = AnonymousUser()
        request.session = {}
        request.COOKIES['interoves_anon'] = 'test-anon-page-key'

        task_groups = _game_task_group_links(game)
        rows = _task_group_rows_skeleton(task_groups, game)
        progress_ctx = _game_page_progress_context(request, game, 'personal')

        self.assertIsNone(rows[0]['progress_text'])
        self.assertEqual(rows[0]['number'], '1')
        self.assertTrue(progress_ctx['load_task_group_progress'])
        self.assertIn('/games/sec_prog/progress/', progress_ctx['task_group_progress_url'])

    def test_progress_api_reports_actor_progress(self):
        game = self._create_section_game('sec_prog2')
        anon_key = 'test-anon-progress-key'
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg2')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='2', name='Two')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='replacements_lines',
                points=2,
                checker_data=json.dumps({'lines': [['a'], ['b'], ['c']]}),
                text='',
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text='x',
                status='Partial',
                points=6,
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/games/sec_prog2/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['2']
        self.assertEqual(row['n_solved'], 1)
        self.assertTrue(row['is_fully_solved'])
        # Полностью решено — прогресс-текст не пишем.
        self.assertIsNone(row['progress_text'])

    def test_progress_api_marks_group_yellow_when_it_has_points(self):
        game = self._create_section_game('sec_scored_progress')
        anon_key = 'test-anon-scored-progress'
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='scored-progress')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                points=10,
                checker=CheckerType.objects.get(pk='equals_with_possible_spaces'),
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text='partial',
                status='Partial',
                points=2,
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/games/sec_scored_progress/progress/')

        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertEqual(row['n_solved'], 0)
        self.assertFalse(row['is_fully_solved'])
        self.assertEqual(row['row_class'], 'new-task--partial')

    def test_progress_api_without_actor_returns_empty(self):
        game = self._create_section_game('sec_prog3')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='tg3')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')

        resp = self.client.get('/games/sec_prog3/progress/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['rows'], {})

    def test_ladder_progress_api_returns_result_squares(self):
        from games.ladder_daily import LADDER_PUBLISH_START_TAG

        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        game.tags = {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        anon_key = 'test-anon-ladder-squares'
        raddle_json = json.dumps({
            'lengths': [3, 3, 3],
            'hints': ['A ____', '____ C'],
            'words': ['CAT', 'DOG', 'BAT'],
        }, ensure_ascii=False)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='ladder_tg_squares')
            GameTaskGroup.objects.filter(game=game).delete()
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='L1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=raddle_json,
                answer='CAT\nDOG\nBAT',
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'word_index': 1, 'word': 'DOG'}),
                status='Ok',
                # Реалистично: tier 2 → credit 0, points=0 при status=Ok.
                # Раньше UI требовал pts >= max и терял квадраты/зелёную подсветку.
                points=0,
                state=json.dumps({
                    'solved_indices': [0, 1, 2],
                    'used_hints': [],
                    'assist_tier': {'1': 2},
                    'total': 0.0,
                }, ensure_ascii=False),
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/ladder/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertTrue(row['is_fully_solved'])
        self.assertEqual(row['row_class'], 'new-task--solved')
        self.assertEqual(row['result_squares'], '🟥')
        self.assertEqual(row['elapsed_label'], '0с')

    def test_ladder_progress_assisted_half_credit_still_solved(self):
        from games.ladder_daily import LADDER_PUBLISH_START_TAG

        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        game.tags = {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        anon_key = 'test-anon-ladder-yellow'
        raddle_json = json.dumps({
            'lengths': [3, 3, 3],
            'hints': ['A ____', '____ C'],
            'words': ['CAT', 'DOG', 'BAT'],
        }, ensure_ascii=False)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='ladder_tg_yellow')
            GameTaskGroup.objects.filter(game=game).delete()
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='L1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=raddle_json,
                answer='CAT\nDOG\nBAT',
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'word_index': 1, 'word': 'DOG'}),
                status='Ok',
                points=0.5,  # tier 1 → half credit, ниже max=1
                state=json.dumps({
                    'solved_indices': [0, 1, 2],
                    'used_hints': [],
                    'assist_tier': {'1': 1},
                    'total': 0.5,
                }, ensure_ascii=False),
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/ladder/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertTrue(row['is_fully_solved'])
        self.assertEqual(row['result_squares'], '🟨')
        self.assertEqual(row['elapsed_label'], '0с')

    def test_ladder_progress_partial_shows_white_squares(self):
        from games.ladder_daily import LADDER_PUBLISH_START_TAG

        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        game.tags = {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        anon_key = 'test-anon-ladder-partial'
        # 5 words → 3 middle squares
        raddle_json = json.dumps({
            'lengths': [3, 3, 3, 3, 3],
            'hints': ['a', 'b', 'c', 'd'],
            'words': ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'],
        }, ensure_ascii=False)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='ladder_tg_partial')
            GameTaskGroup.objects.filter(game=game).delete()
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='L1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=raddle_json,
                answer='AAA\nBBB\nCCC\nDDD\nEEE',
            )
            Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'word_index': 1, 'word': 'BBB'}),
                status='Partial',
                points=1,
                state=json.dumps({
                    'solved_indices': [0, 1, 4],
                    'used_hints': [],
                    'assist_tier': {},
                    'total': 1.0,
                }, ensure_ascii=False),
            )

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/ladder/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertFalse(row['is_fully_solved'])
        self.assertEqual(row['row_class'], 'new-task--partial')
        self.assertEqual(row['result_squares'], '🟩⬜⬜')
        self.assertIsNone(row['progress_text'])
        self.assertIsNone(row['elapsed_label'])

    def test_ladder_progress_api_returns_elapsed_from_attempts(self):
        from games.ladder_daily import LADDER_PUBLISH_START_TAG

        game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(game)
        game.tags = {LADDER_PUBLISH_START_TAG: '2026-07-08T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        anon_key = 'test-anon-ladder-elapsed'
        raddle_json = json.dumps({
            'lengths': [3, 3, 3],
            'hints': ['A ____', '____ C'],
            'words': ['CAT', 'DOG', 'BAT'],
        }, ensure_ascii=False)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='ladder_tg_elapsed')
            GameTaskGroup.objects.filter(game=game).delete()
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='L1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='raddle',
                points=1,
                checker_data=raddle_json,
                answer='CAT\nDOG\nBAT',
            )
            first = Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'word_index': 1, 'word': 'DOG'}),
                status='Partial',
                points=0,
                state=json.dumps({
                    'solved_indices': [0, 1],
                    'used_hints': [],
                    'assist_tier': {},
                    'total': 0.0,
                }, ensure_ascii=False),
            )
            last = Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'word_index': 1, 'word': 'DOG'}),
                status='Ok',
                points=0,
                state=json.dumps({
                    'solved_indices': [0, 1, 2],
                    'used_hints': [],
                    'assist_tier': {'1': 2},
                    'total': 0.0,
                }, ensure_ascii=False),
            )
        t0 = timezone.now() - timedelta(seconds=226)
        Attempt.manager.filter(pk=first.pk).update(time=t0)
        Attempt.manager.filter(pk=last.pk).update(time=t0 + timedelta(seconds=226))

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/ladder/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertTrue(row['is_fully_solved'])
        self.assertEqual(row['result_squares'], '🟥')
        self.assertEqual(row['elapsed_label'], '3м 46с')

    def test_salad_progress_api_returns_squares_and_elapsed(self):
        from games.word_salad import WORD_SALAD_GAME_ID
        from games.word_salad_daily import WORD_SALAD_PUBLISH_START_TAG

        game = Game.objects.filter(id=WORD_SALAD_GAME_ID, project_id='sections').first()
        self.assertIsNotNone(game)
        game.tags = {WORD_SALAD_PUBLISH_START_TAG: '2026-08-23T00:00:00+03:00'}
        game.save(update_fields=['tags'])

        anon_key = 'test-anon-salad-archive'
        salad_json = json.dumps({
            'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
            'words': ['PONM', 'ABCD'],
        }, ensure_ascii=False)
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='salad_tg_archive')
            GameTaskGroup.objects.filter(game=game).delete()
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='S1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='word_salad',
                checker=CheckerType.objects.get(pk='word_salad'),
                points=1,
                checker_data=salad_json,
                text='Города',
            )
            first = Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'action': 'hint', 'word_index': 0}),
                status='Wrong',
                points=0,
                state=json.dumps({
                    'solved_indices': [],
                    'hint_counts': {'0': 2},
                }, ensure_ascii=False),
            )
            last = Attempt.manager.create(
                task=task,
                anon_key=anon_key,
                game=game,
                text=json.dumps({'action': 'solve', 'path': [12, 13, 14, 15]}),
                status='Ok',
                points=1,
                state=json.dumps({
                    'solved_indices': [0, 1],
                    'hint_counts': {'0': 2},
                }, ensure_ascii=False),
            )
        t0 = timezone.now() - timedelta(seconds=226)
        Attempt.manager.filter(pk=first.pk).update(time=t0)
        Attempt.manager.filter(pk=last.pk).update(time=t0 + timedelta(seconds=226))

        self.client.cookies['interoves_anon'] = anon_key
        resp = self.client.get('/salad/progress/')
        self.assertEqual(resp.status_code, 200)
        row = resp.json()['rows']['1']
        self.assertTrue(row['is_fully_solved'])
        self.assertEqual(row['row_class'], 'new-task--solved')
        # Alphabetical display: ABCD (no hints) then PONM (2 hints).
        self.assertEqual(row['result_squares'], '🟩2️⃣')
        self.assertEqual(row['elapsed_label'], '3м 46с')

    def test_bulk_actor_attempts_infos_uses_two_queries_and_isolates_actor(self):
        game = self._create_section_game('bulk_actor_progress')
        actor_key = 'bulk-actor-a'
        other_key = 'bulk-actor-b'
        tasks = []
        with patch('games.views.track.track_task_change'):
            for number in range(1, 7):
                tg = TaskGroup.objects.create(label='bulk-actor-{}'.format(number))
                task = Task.objects.create(
                    task_group=tg,
                    number='1',
                    points=1,
                    checker=CheckerType.objects.get(pk='equals_with_possible_spaces'),
                )
                tasks.append(task)
                Attempt.manager.create(
                    task=task,
                    game=game,
                    anon_key=actor_key,
                    text='actor-a',
                    status='Ok',
                    points=1,
                )
                Attempt.manager.create(
                    task=task,
                    game=game,
                    anon_key=other_key,
                    text='actor-b',
                    status='Wrong',
                    points=0,
                )

        with CaptureQueriesContext(connection) as queries:
            infos = Attempt.manager.get_bulk_actor_attempts_infos(
                [task.id for task in tasks],
                anon_key=actor_key,
                game=game,
            )

        self.assertEqual(len(queries), 2)
        self.assertEqual(set(infos), {task.id for task in tasks})
        for info in infos.values():
            self.assertTrue(info.is_solved())
            self.assertEqual([attempt.text for attempt in info.attempts], ['actor-a'])

    def test_salad_progress_query_count_does_not_scale_per_archive_row(self):
        from games.word_salad import WORD_SALAD_GAME_ID

        game = Game.objects.get(id=WORD_SALAD_GAME_ID, project_id='sections')
        anon_key = 'bulk-salad-progress'
        salad_json = json.dumps({
            'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
            'words': ['ABCD'],
        })
        with patch('games.views.track.track_task_change'):
            GameTaskGroup.objects.filter(game=game).delete()
            for number in range(1, 7):
                tg = TaskGroup.objects.create(label='bulk-salad-{}'.format(number))
                GameTaskGroup.objects.create(
                    game=game,
                    task_group=tg,
                    number=str(number),
                    name='S{}'.format(number),
                )
                task = Task.objects.create(
                    task_group=tg,
                    number='1',
                    task_type='word_salad',
                    checker=CheckerType.objects.get(pk='word_salad'),
                    points=1,
                    checker_data=salad_json,
                )
                Attempt.manager.create(
                    task=task,
                    game=game,
                    anon_key=anon_key,
                    text='done',
                    status='Ok',
                    points=1,
                    state=json.dumps({'solved_indices': [0], 'hint_counts': {}}),
                )
        task_groups = list(_game_task_group_links(game))

        with CaptureQueriesContext(connection) as queries:
            rows = _task_group_progress_payload(
                game,
                task_groups,
                anon_key=anon_key,
            )

        # Bulk DailySolveTiming lookup is +1 vs the pre-timing budget of 8.
        self.assertLessEqual(len(queries), 9)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row['is_fully_solved'] for row in rows.values()))

    def test_initial_salad_page_embeds_actor_progress_without_json_fetch(self):
        from games.word_salad import WORD_SALAD_GAME_ID
        from games.word_salad_daily import WORD_SALAD_PUBLISH_START_TAG

        _ensure_login_modal_deps()
        game = Game.objects.get(id=WORD_SALAD_GAME_ID, project_id='sections')
        game.tags = {WORD_SALAD_PUBLISH_START_TAG: '2026-08-23T00:00:00+03:00'}
        game.save(update_fields=['tags'])
        actor_key = 'ssr-salad-actor'
        salad_json = json.dumps({
            'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
            'words': ['ABCD', 'PONM'],
        })
        with patch('games.views.track.track_task_change'):
            GameTaskGroup.objects.filter(game=game).delete()
            tg = TaskGroup.objects.create(label='ssr-salad')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='S1')
            task = Task.objects.create(
                task_group=tg,
                number='1',
                task_type='word_salad',
                checker=CheckerType.objects.get(pk='word_salad'),
                points=1,
                checker_data=salad_json,
                text='SSR theme',
            )
            Attempt.manager.create(
                task=task,
                game=game,
                anon_key=actor_key,
                text='done',
                status='Ok',
                points=2,
                state=json.dumps({'solved_indices': [0, 1], 'hint_counts': {}}),
            )

        self.client.cookies['interoves_anon'] = actor_key
        response = self.client.get('/salad/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['task_group_progress_embedded'])
        self.assertFalse(response.context['load_task_group_progress'])
        html = response.content.decode('utf-8')
        self.assertIn('data-fully-solved="1"', html)
        self.assertIn('🟩🟩', html)
        self.assertNotIn('/salad/progress/', html)

        self.client.cookies['interoves_anon'] = 'different-salad-actor'
        other_response = self.client.get('/salad/')
        other_html = other_response.content.decode('utf-8')
        self.assertIn('data-fully-solved="0"', other_html)
        self.assertNotIn('🟩🟩', other_html)

    def test_initial_progress_keeps_json_fallback_when_server_projection_fails(self):
        game = self._create_section_game('progress_fallback')
        with patch('games.views.track.track_task_change'):
            tg = TaskGroup.objects.create(label='progress-fallback')
            GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='One')
        task_groups = list(_game_task_group_links(game))
        rows = _task_group_rows_skeleton(task_groups, game)
        request = RequestFactory().get('/games/progress_fallback/')
        request.user = AnonymousUser()
        request.session = {}
        request.COOKIES['interoves_anon'] = 'fallback-actor'

        with patch(
            'games.views.new_ui._task_group_progress_payload',
            side_effect=RuntimeError('projection failed'),
        ), self.assertLogs('games.views.new_ui', level='ERROR'):
            hydrated_rows, progress_context = _initial_task_group_progress(
                request,
                game,
                'personal',
                task_groups,
                rows,
            )

        self.assertIs(hydrated_rows, rows)
        self.assertTrue(progress_context['load_task_group_progress'])
        self.assertIn('/games/progress_fallback/progress/', progress_context['task_group_progress_url'])
