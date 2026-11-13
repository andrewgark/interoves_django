from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import caches
from django.test import Client, TestCase
from django.urls import reverse

from games.community import game_month
from games.community.stats import head_to_head
from games.models import (
    ChainTaskState,
    CheckerType,
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyAggregateSnapshotVersion,
    DailyResultProjection,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    Profile,
    Project,
    TaskGroup,
    Team,
)
from games.results.standings.aggregate_leaderboard import ReleaseColumn

GAME_ID = 'ladder'
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc)


class GameMonthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        # A flushed --keepdb database loses migration data: make it explicit.
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        cls.game, _ = Game.objects.get_or_create(
            id=GAME_ID, defaults={'name': 'Лесенка', 'author': 'test', 'author_extra': '', 'project_id': 'main'},
        )
        cls.owner = User.objects.create_user('gm_owner', password='secret')
        cls.viewer = User.objects.create_user('gm_viewer', password='secret')
        cls.stranger = User.objects.create_user('gm_stranger', password='secret')
        cls.profile = Profile.objects.create(user=cls.owner, first_name='Анна', last_name='Петрова')
        Profile.objects.create(user=cls.viewer, first_name='Вера', last_name='Зорина')
        Profile.objects.create(user=cls.stranger, first_name='Сая', last_name='Ли')
        cls.groups, cls.links = [], []
        with patch('games.realtime.track_compat.track_task_change'):
            for number in (1, 2, 3):
                group = TaskGroup.objects.create(label='gm-{}'.format(number))
                cls.groups.append(group)
                cls.links.append(GameTaskGroup.objects.create(
                    game=cls.game, task_group=group, number=str(number), name=str(number),
                ))
        # Анна wrote the third release: it is out of her percentage.
        cls.groups[2].authors.add(cls.profile)

    def setUp(self):
        caches['daily_statistics'].clear()

    def columns(self):
        return [
            ReleaseColumn(
                link=link, max_score=10.0, label=link.number,
                url='/ladder/{}/results/'.format(link.number),
                published_at=datetime(2026, 11, int(link.number), 0, 0, tzinfo=dt_timezone.utc),
            )
            for link in reversed(self.links)
        ]

    def play(self, user, index, score, ms=None, solved=True):
        group = self.groups[index]
        DailyResultProjection.objects.create(
            game=self.game, task_group=group, actor_type='user',
            actor_key=str(user.pk), user=user, score=score,
        )
        row = PlayerCompletedGame.objects.create(
            user=user, game=self.game, task_group=group, game_kind=GAME_ID,
            game_instance_id='{}:{}:{}'.format(self.game.pk, group.pk, user.pk),
            result=PlayerCompletedGame.RESULT_SOLVED if solved else PlayerCompletedGame.RESULT_FAILED,
        )
        PlayerCompletedGame.objects.filter(pk=row.pk).update(completed_at=NOW)
        if ms is not None:
            DailySolveTiming.objects.create(
                game=self.game, task_group=group, user=user,
                status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=ms, frozen_ms=ms,
                timing_version=2,
            )

    def month(self, viewer=None):
        monthly = (self.columns(), date(2026, 11, 1), date(2026, 10, 1), None, {})
        with patch(
            'games.results.standings.aggregate_leaderboard._monthly_columns', return_value=monthly,
        ):
            return game_month.month_view(
                game_month.user_actor(self.owner), GAME_ID, '2026-11',
                viewer=game_month.user_actor(viewer) if viewer else None, now=NOW,
            )

    def test_percentage_skips_authored_releases_and_counts_misses(self):
        self.play(self.owner, 0, 10, ms=100_000)
        self.play(self.owner, 2, 10, ms=50_000)  # own release: not counted
        data = self.month()
        kpi = data['kpi']
        # Release 2 was missed: 10 of 20 possible.
        self.assertEqual(kpi['score_pct'], 50)
        self.assertEqual((kpi['played'], kpi['eligible'], kpi['authored']), (1, 2, 1))
        self.assertEqual([r['n'] for r in data['releases']], [1, 2, 3])
        self.assertEqual([r['author'] for r in data['releases']], [False, False, True])
        self.assertIsNone(data['releases'][1]['p'])
        self.assertEqual(data['releases'][0]['p']['time'], 100)
        self.assertEqual(data['releases'][0]['p']['score'], 100.0)
        self.assertEqual((data['month'], data['prev'], data['next']), ('2026-11', '2026-10', None))

    def test_median_uses_public_solvers_only(self):
        self.play(self.owner, 0, 10, ms=100_000)
        self.play(self.viewer, 0, 10, ms=300_000)
        self.play(self.stranger, 0, 4, ms=None, solved=False)
        self.play(self.owner, 2, 10, ms=10_000)  # author: out of the cohort
        self.play(self.viewer, 2, 10, ms=40_000)
        releases = self.month()['releases']
        self.assertEqual(releases[0]['m']['time'], 200)
        self.assertEqual(releases[0]['m']['n'], 2)
        # Score median covers every public player, solvers or not.
        self.assertEqual(releases[0]['m']['score'], 100.0)
        self.assertEqual(releases[2]['m']['time'], 40)
        self.assertIsNone(releases[1]['m']['time'])

    def test_viewer_series_and_monthly_duel(self):
        self.play(self.owner, 0, 10, ms=100_000)
        self.play(self.viewer, 0, 10, ms=200_000)  # same score, viewer slower
        self.play(self.owner, 1, 5, ms=100_000)
        self.play(self.viewer, 1, 8, ms=300_000)  # viewer scores more
        data = self.month(viewer=self.viewer)
        self.assertEqual(data['releases'][0]['v']['time'], 200)
        duel = data['kpi']['duel']
        self.assertEqual((duel['common'], duel['wins'], duel['ties'], duel['losses']), (2, 1, 0, 1))
        self.assertEqual(duel['wins_pct'], 50)
        # The all-time duel counts the same releases per game.
        overall = head_to_head(self.viewer, self.owner)
        self.assertEqual(overall.by_game[GAME_ID].wins, 1)
        self.assertEqual(overall.by_game[GAME_ID].losses, 1)

    def test_places_by_score_and_with_tiebreak(self):
        self.play(self.owner, 0, 10, ms=100_000)
        snapshot = DailyAggregateSnapshot.objects.create(game=self.game, month=date(2026, 11, 1))
        version = DailyAggregateSnapshotVersion.objects.create(snapshot=snapshot, published_at=NOW)
        DailyAggregateSnapshot.objects.filter(pk=snapshot.pk).update(published_version=version)
        for user, score, time_ms in (
            (self.owner, 15, 300_000), (self.viewer, 15, 200_000), (self.stranger, 20, 900_000),
        ):
            DailyAggregateSnapshotRow.objects.create(
                version=version, actor_type='user', actor_key=str(user.pk), user=user,
                score=score, total_time_ms=time_ms,
            )
        places = self.month()['kpi']['places']
        self.assertEqual(places, {
            'place': 2, 'place_tie': 3, 'total': 3, 'tie_label': 'с учётом времени',
        })

    def test_endpoint_respects_page_visibility(self):
        self.play(self.owner, 0, 10, ms=100_000)
        url = reverse('new_player_game_month', kwargs={
            'public_id': self.profile.public_id, 'game_id': GAME_ID, 'month': '2026-11',
        })
        monthly = (self.columns(), date(2026, 11, 1), None, None, {})
        with patch('games.results.standings.aggregate_leaderboard._monthly_columns', return_value=monthly):
            response = Client().get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()['releases'][0]['v'])
            viewer = Client()
            viewer.login(username='gm_viewer', password='secret')
            # Another signed-in player gets a duel even before playing together.
            self.assertEqual(viewer.get(url).json()['kpi']['duel']['common'], 0)
            Profile.objects.filter(pk=self.profile.pk).update(page_hidden=True)
            self.assertEqual(Client().get(url).status_code, 404)
            self.assertEqual(Client().get(url.replace(GAME_ID, 'nosuchgame')).status_code, 404)

    def test_team_endpoint_hides_hidden_teams_from_strangers(self):
        team = Team.objects.create(name='gm_team', visible_name='Сфинксы', is_hidden=True)
        url = reverse('new_team_game_month', kwargs={
            'public_id': team.public_id, 'game_id': GAME_ID, 'month': 'latest',
        })
        self.assertEqual(Client().get(url).status_code, 404)

    def test_hint_counts_for_ladder_come_from_task_projection(self):
        from games.models import DailyTaskResultProjection, Task

        with patch('games.realtime.track_compat.track_task_change'):
            task = Task.objects.create(
                task_group=self.groups[0], number='1', task_type='raddle', points=10,
                checker_data='{}', text='',
            )
        self.play(self.owner, 0, 10, ms=100_000)
        DailyTaskResultProjection.objects.create(
            game=self.game, task_group=self.groups[0], task=task, actor_type='user',
            actor_key=str(self.owner.pk), user=self.owner, hint_numbers=[1, 2],
        )
        self.assertEqual(self.month()['releases'][0]['p']['hints'], 2)


    def test_profile_page_shows_daily_stats_with_charts(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        for provider in ('google', 'vk', 'yandex'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider, defaults={'name': provider, 'client_id': 'x', 'secret': 'x'},
            )
            app.sites.add(site)
        self.play(self.owner, 0, 10, ms=100_000)
        client = Client()
        client.login(username='gm_owner', password='secret')
        body = client.get(reverse('new_profile')).content.decode()
        self.assertIn('Ежедневные игры', body)
        self.assertIn('data-pgs-card', body)
        self.assertIn('/u/{}/games/GAME/MONTH/'.format(self.profile.public_id), body)

    def test_warm_precomputes_month_medians_once_per_interval(self):
        self.play(self.owner, 0, 10, ms=100_000)
        self.play(self.viewer, 0, 10, ms=300_000)
        monthly = (self.columns(), date(2026, 11, 1), date(2026, 10, 1), None, {})
        with patch('games.results.standings.aggregate_leaderboard._monthly_columns', return_value=monthly), \
             patch('games.community.game_month.month_games', return_value=[GAME_ID]):
            first = game_month.warm_release_baselines(now=NOW)
            again = game_month.warm_release_baselines(now=NOW)
        self.assertEqual(first, {'skipped': False, 'releases': {GAME_ID: 3}})
        self.assertEqual(again, {'skipped': True})
        cached = caches['daily_statistics'].get(game_month._baseline_key(self.game, self.groups[0].pk))
        self.assertEqual(cached['time'], 200)

    def test_author_who_never_plays_still_gets_the_game_tab(self):
        from games.community.stats import player_stats

        # Анна played nothing of Лесенка but wrote release 3; some other game
        # gives her a played game so the block shows at all.
        self.play(self.owner, 0, 10, ms=100_000)
        DailyResultProjection.objects.filter(user=self.owner).delete()
        PlayerCompletedGame.objects.filter(user=self.owner).update(game_id='walls')
        Game.objects.get_or_create(id='walls', defaults={'name': 'Стенки', 'author': 't', 'author_extra': '', 'project_id': 'main'})
        with patch('games.daily.timing.streak.daily_streaks_for_actor', return_value={}), \
             patch('games.community.game_month.authored_release_count', return_value=1):
            games = {g.game_id: g for g in player_stats(self.owner, now=NOW).games}
        self.assertIn(GAME_ID, games)
        self.assertEqual((games[GAME_ID].played, games[GAME_ID].authored), (0, 1))

class CensorlyHintTests(TestCase):
    def test_hints_come_from_chain_state_key(self):
        from games.community.game_month import _hint_counts
        from games.models import Task

        Project.objects.get_or_create(pk='main', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
        game = Game.objects.create(id='gm_censorly', name='Ц', author='t', author_extra='', project_id='main')
        user = User.objects.create_user('gm_hinter')
        with patch('games.realtime.track_compat.track_task_change'):
            group = TaskGroup.objects.create(label='gm-c')
            task = Task.objects.create(task_group=group, number='1', task_type='censorly', checker_data='{}', text='')
        ChainTaskState.objects.create(
            task=task, game=game, user=user, game_mode='general', state='{"hints_taken": 3, "guesses": []}',
        )
        counts = _hint_counts(game, 'censorly', [group.pk])
        self.assertEqual(counts[(group.pk, ('user', str(user.pk)))], 3)

