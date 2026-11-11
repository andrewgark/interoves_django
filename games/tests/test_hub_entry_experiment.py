from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from games.models import (
    CheckerType,
    Game,
    HTMLPage,
    PlayerAnalyticsState,
    PlayerCompletedGame,
    PlayerStartedGame,
    Project,
    Task,
    TaskGroup,
)
from games.ops.analytics import hub_entry_experiment as hub
from games.ops.analytics.events import register_started_game
from games.ops.analytics.hub_entry_experiment import (
    HUB_ENTRY_COOKIE_NAME,
    apply_newcomer_entry_links,
    experiment_summary,
)
from games.ops.analytics.identity import attach_anon_cookie
from games.tests.test_onboarding import _ensure_social_apps

ANON = '5d0c3d0e-2222-4c2d-9e8f-000000000002'
SEED = 'games.ops.analytics.hub_entry_experiment._seed_variant'


class HubEntryExperimentPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _ensure_social_apps()
        cls.game = Game.objects.get(pk='salad')
        cls.task_group = TaskGroup.objects.create(label='hub-entry-marker')

    def setUp(self):
        self.client = Client()
        attach_anon_cookie(self.client, ANON)

    def test_first_arm_links_newcomer_puzzle_sets_cookie_and_writes_nothing(self):
        with patch(SEED, return_value='first'):
            response = self.client.get('/')
        self.assertEqual(response.context['onboarding_salad_url'], '/salad/first/')
        self.assertContains(response, 'href="/salad/first/"')
        self.assertIn(HUB_ENTRY_COOKIE_NAME, response.cookies)
        self.assertFalse(PlayerAnalyticsState.objects.exclude(hub_entry_variant='').exists())

    def test_the_cookie_keeps_the_arm(self):
        with patch(SEED, return_value='today'):
            first = self.client.get('/')
        with patch(SEED, return_value='first'):
            second = self.client.get('/')
        self.assertNotEqual(first.context['onboarding_salad_url'], '/salad/first/')
        self.assertNotEqual(second.context['onboarding_salad_url'], '/salad/first/')
        self.assertNotIn(HUB_ENTRY_COOKIE_NAME, second.cookies)

    def test_returning_player_gets_no_arm(self):
        PlayerStartedGame.objects.create(
            game=self.game, task_group=self.task_group, game_kind='salad',
            game_instance_id='salad:hub-entry', public_game_id='salad:1', anon_key=ANON,
        )
        response = self.client.get('/')
        self.assertNotIn(HUB_ENTRY_COOKIE_NAME, response.cookies)


class HubEntryEnrollmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name)
        CheckerType.objects.get_or_create(id='equals_with_possible_spaces')
        project, _ = Project.objects.get_or_create(id='main')
        cls.game = Game.objects.create(
            id='hub-enroll-game', name='h', author='t', project=project,
            requires_ticket=False, is_tournament=False, is_ready=True,
        )

    def _task(self, n):
        group = TaskGroup.objects.create(label=f'hub-enroll-{n}')
        return Task.objects.create(
            task_group=group, number='1', task_type='default',
            checker_id='equals_with_possible_spaces', answer='X',
        )

    def _start(self, n, entry, anon=ANON):
        token = hub._current_entry.set(entry)
        try:
            register_started_game(task=self._task(n), game=self.game, anon_key=anon)
        finally:
            hub.release_hub_entry(token)

    def test_first_game_enrolls_with_the_arm_and_first_view_time(self):
        self._start(1, {'v': 'first', 'at': 1762560000})
        state = PlayerAnalyticsState.objects.get(anon_key=ANON)
        self.assertEqual(state.hub_entry_variant, 'first')
        self.assertEqual(int(state.hub_entry_viewed_at.timestamp()), 1762560000)

    def test_no_cookie_or_earlier_game_means_no_enrollment(self):
        self._start(2, None)
        self._start(3, {'v': 'first', 'at': 1762560000})
        self.assertFalse(PlayerAnalyticsState.objects.exclude(hub_entry_variant='').exists())


class ApplyNewcomerEntryLinksTests(TestCase):
    def test_daily_cards_link_first_puzzle_without_today_state(self):
        cards = [
            {'id': 'salad', 'play_url': '/salad/80/', 'cta_label': 'Сегодняшний Салатик', 'is_today': True,
             'cta_number': '80', 'is_solved': True, 'load_task_group_progress': True},
            {'id': 'ladder', 'play_url': None, 'cta_label': ''},
            {'id': 'walls', 'play_url': '/games/walls/3/', 'cta_label': 'Последняя стена'},
        ]
        apply_newcomer_entry_links(cards)
        salad, ladder, walls = cards
        self.assertEqual(salad['play_url'], '/salad/first/')
        self.assertEqual(salad['cta_label'], 'Начать Салатик')
        self.assertFalse(salad['is_today'])
        self.assertIsNone(salad['cta_number'])
        self.assertFalse(salad['is_solved'])
        self.assertFalse(salad['load_task_group_progress'])
        self.assertIsNone(ladder['play_url'])
        self.assertEqual(walls['play_url'], '/games/walls/3/')


class ExperimentSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = Game.objects.get(pk='salad')
        cls.groups = [TaskGroup.objects.create(label=f'hub-entry-{i}') for i in range(3)]

    def _start(self, anon, group, at, *, done=False):
        instance = f'salad:{group.pk}'
        row = PlayerStartedGame.objects.create(
            game=self.game, task_group=group, game_kind='salad', game_instance_id=instance,
            public_game_id=instance, anon_key=anon,
        )
        PlayerStartedGame.objects.filter(pk=row.pk).update(started_at=at)
        if done:
            PlayerCompletedGame.objects.create(
                game=self.game, task_group=group, game_kind='salad', game_instance_id=instance,
                public_game_id=instance, result=PlayerCompletedGame.RESULT_SOLVED, anon_key=anon,
            )

    def test_counts_start_first_finish_and_closed_window_returns(self):
        now = timezone.now()
        viewed = now - timedelta(days=20)
        for anon, arm in (('a-ret', 'first'), ('a-once', 'first'), ('a-idle', 'today'), ('a-new', 'today')):
            PlayerAnalyticsState.objects.create(
                anon_key=anon, hub_entry_variant=arm,
                hub_entry_viewed_at=now - timedelta(hours=2) if anon == 'a-new' else viewed,
            )
        self._start('a-ret', self.groups[0], viewed + timedelta(minutes=5), done=True)
        self._start('a-ret', self.groups[1], viewed + timedelta(days=2))
        self._start('a-once', self.groups[0], viewed + timedelta(minutes=5))
        self._start('a-new', self.groups[2], now - timedelta(hours=1), done=True)

        summary = experiment_summary(now=now)

        self.assertEqual(summary['first'], {
            'players': 2, 'finished_first': 1, 'return_eligible': 2, 'returned': 1,
        })
        # 'a-idle' was enrolled on view by the first version and never played.
        self.assertEqual(summary['today'], {
            'players': 1, 'finished_first': 1, 'return_eligible': 0, 'returned': 0,
        })
