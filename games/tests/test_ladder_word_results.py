import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from games.ladder_daily import LADDER_GAME_ID, LADDER_PUBLISH_START_TAG
from games.ladder_word_results import (
    build_ladder_word_results_context,
    ladder_word_results_headers_context,
)
from games.models import (
    Attempt,
    CheckerType,
    Game,
    GameTaskGroup,
    HTMLPage,
    PersonalResultsParticipant,
    Project,
    Task,
    TaskGroup,
)
from games.views.new_ui import new_ladder_word_results_page

MINI_LADDER = {
    'lengths': [3, 3, 3, 3],
    'hints': ['A ____', '____ C', '____ D'],
    'words': ['AAA', 'BBB', 'CCC', 'DDD'],
}


def _ensure_fixtures():
    Project.objects.get_or_create(pk='main', defaults={})
    Project.objects.get_or_create(pk='sections', defaults={})
    for name in (
        'Правила Десяточки',
        'Правила турнирного режима',
        'Правила тренировочного режима',
    ):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    CheckerType.objects.get_or_create(pk='raddle')


class LadderWordResultsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_fixtures()
        cls.ladder = Game.objects.get(pk=LADDER_GAME_ID)
        cls.ladder.tags = {
            **(cls.ladder.tags or {}),
            LADDER_PUBLISH_START_TAG: '2020-01-01T00:00:00+03:00',
        }
        cls.ladder.is_ready = True
        cls.ladder.save(update_fields=['tags', 'is_ready'])

        with patch('games.views.track.track_task_change'):
            cls.tg = TaskGroup.objects.create(label='ladder_word_res_tg')
            cls.placement = GameTaskGroup.objects.create(
                game=cls.ladder, task_group=cls.tg, number='9100', name='Лесенка #9100',
            )
            cls.task = Task.objects.create(
                task_group=cls.tg,
                number='1',
                task_type='raddle',
                checker=CheckerType.objects.get(pk='raddle'),
                points=1,
                checker_data=json.dumps(MINI_LADDER, ensure_ascii=False),
                answer='AAA\nBBB\nCCC\nDDD',
            )

    def setUp(self):
        self.factory = RequestFactory()

    def _make_attempt(self, *, anon_key, state, points=0, status='Partial'):
        return Attempt.manager.create(
            task=self.task,
            game=self.ladder,
            anon_key=anon_key,
            text='{}',
            status=status,
            points=points,
            state=json.dumps(state, ensure_ascii=False),
            time=timezone.now(),
        )

    def test_headers_have_middle_word_columns(self):
        # 4 words → 2 middle columns
        ctx = ladder_word_results_headers_context(self.task)
        self.assertEqual(ctx['ladder_word_count'], 2)
        self.assertEqual([h.number for h in ctx['task_groups']], ['1', '2'])

    def test_cells_reflect_tiers_and_score(self):
        # word1 (index1) green, word2 (index2) yellow → score 1.5
        self._make_attempt(
            anon_key='lw-anon-1',
            state={
                'solved_indices': [0, 1, 2, 3],
                'used_hints': [],
                'assist_tier': {'2': 1},
                'total': 1.5,
            },
            points=1.5,
            status='Ok',
        )
        ctx = build_ladder_word_results_context(self.ladder, self.placement, self.task)
        self.assertEqual(len(ctx['teams_sorted']), 1)
        p = ctx['teams_sorted'][0]
        self.assertIsInstance(p, PersonalResultsParticipant)
        self.assertEqual(ctx['team_to_score'][p], 1.5)
        cells = ctx['team_to_cells'][p]
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[0]['result_points'], 1.0)
        self.assertEqual(cells[0]['hint_numbers'], [])
        self.assertEqual(cells[0]['cls'], 'cell-full')
        self.assertEqual(cells[1]['result_points'], 0.5)
        self.assertEqual(cells[1]['hint_numbers'], [1])
        self.assertEqual(cells[1]['cls'], 'cell-partial')

    def test_answer_assist_shows_hints_1_and_2(self):
        self._make_attempt(
            anon_key='lw-anon-2',
            state={
                'solved_indices': [0, 1, 3],
                'used_hints': [],
                'assist_tier': {'1': 2},
                'total': 0,
            },
            points=0,
            status='Partial',
        )
        ctx = build_ladder_word_results_context(self.ladder, self.placement, self.task)
        p = ctx['teams_sorted'][0]
        cells = ctx['team_to_cells'][p]
        self.assertEqual(cells[0]['result_points'], 0)
        self.assertEqual(cells[0]['hint_numbers'], [1, 2])
        self.assertEqual(cells[0]['cls'], 'cell-zero')
        self.assertEqual(cells[1]['n_attempts'], 0)
        self.assertEqual(cells[1]['hint_numbers'], [])

    def test_page_renders_progressive_headers(self):
        request = self.factory.get('/ladder/9100/results/')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.views.new_ui.is_ladder_number_published', return_value=True):
            with patch('games.views.new_ui.render') as render_mock:
                new_ladder_word_results_page(request, '9100')
                ctx = render_mock.call_args[0][2]
                self.assertTrue(ctx['is_ladder_word_results'])
                self.assertTrue(ctx['progressive_results'])
                self.assertEqual(ctx['teams_sorted'], [])
                self.assertEqual(len(ctx['task_groups']), 2)
                self.assertEqual(ctx['back_url'], '/ladder/9100/')

    def test_partial_loads_rows(self):
        self._make_attempt(
            anon_key='lw-anon-3',
            state={
                'solved_indices': [0, 1, 3],
                'used_hints': [],
                'assist_tier': {},
                'total': 1,
            },
            points=1,
            status='Partial',
        )
        request = self.factory.get('/ladder/9100/results/?page=1&partial=1')
        request.user = AnonymousUser()
        request.session = {}
        with patch('games.views.new_ui.is_ladder_number_published', return_value=True):
            with patch('games.views.new_ui.render') as render_mock:
                new_ladder_word_results_page(request, '9100')
                self.assertEqual(render_mock.call_args[0][1], 'new/partials/results_rows.html')
                ctx = render_mock.call_args[0][2]
                self.assertEqual(len(ctx['teams_sorted']), 1)
                self.assertTrue(ctx['is_ladder_word_results'])

    def test_unpublished_ladder_404(self):
        with patch('games.views.new_ui.is_ladder_number_published', return_value=False):
            request = self.factory.get('/ladder/9100/results/')
            request.user = AnonymousUser()
            request.session = {}
            with self.assertRaises(Http404):
                new_ladder_word_results_page(request, '9100')

    def test_staff_can_open_unpublished_ladder_results(self):
        staff = get_user_model().objects.create_user(
            username='ladder-results-staff',
            password='secret',
            is_staff=True,
        )
        request = self.factory.get('/ladder/9100/results/')
        request.user = staff
        request.session = {}
        with patch('games.views.new_ui.is_ladder_number_published', return_value=False):
            with patch('games.views.new_ui.render') as render_mock:
                new_ladder_word_results_page(request, '9100')
        self.assertEqual(render_mock.call_args[0][1], 'ui/results.html')
