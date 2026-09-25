from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from games.aggregate_leaderboard import build_aggregate_page
from games.models import (
    DailyResultProjection, DailyResultProjectionState, DailySolveTiming, Game,
    GameTaskGroup, HTMLPage, PersonalResultsParticipant, Profile, Project,
    ReplaySlot, Task, TaskGroup, Team,
)


class AggregateLeaderboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={'name': 'Sections'})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

    def setUp(self):
        self.game = Game.objects.create(
            id='agg_test', name='Aggregate test', author='test', author_extra='',
            project_id='sections', is_ready=True,
        )
        self.factory = RequestFactory()
        self.links = []
        with patch('games.views.track.track_task_change'):
            for number, maximum in ((1, 5), (2, 15)):
                group = TaskGroup.objects.create(label='aggregate-{}'.format(number))
                link = GameTaskGroup.objects.create(
                    game=self.game, task_group=group, number=str(number), name='Release {}'.format(number),
                )
                task = Task.objects.create(
                    task_group=group, number='1', task_type='default', points=maximum,
                    checker_data='answer', text='Question',
                )
                self.links.append((link, task))

    def _request(self, query=''):
        request = self.factory.get('/{}{}results/{}'.format(self.game.id, '/' if query else '/', query))
        request.user = AnonymousUser()
        request.session = {}
        return request

    @staticmethod
    def _info(points, at=None):
        return SimpleNamespace(
            attempts=[SimpleNamespace(time=at or timezone.now())],
            hint_attempts=[], get_result_points=lambda: points,
        )

    def test_release_windows_limits_cursor_and_stable_anchor(self):
        with patch('games.views.track.track_task_change'):
            for n in range(3, 33):
                group = TaskGroup.objects.create(label='aggregate-{}'.format(n))
                GameTaskGroup.objects.create(game=self.game, task_group=group, number=str(n), name=str(n))
                Task.objects.create(task_group=group, number='1', points=1, text='x', checker_data='x')
        empty = patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value={})
        with empty:
            default = build_aggregate_page(self._request(), self.game)
            twenty = build_aggregate_page(self._request('?limit=20'), self.game)
            thirty = build_aggregate_page(self._request('?limit=30'), self.game)
            invalid = build_aggregate_page(self._request('?limit=999'), self.game)
        self.assertEqual(len(default['aggregate_columns']), 10)
        self.assertEqual(default['aggregate_columns'][0].link.number, '32')
        self.assertEqual(len(twenty['aggregate_columns']), 20)
        self.assertEqual(twenty['aggregate_columns'][0].link.number, '32')
        self.assertEqual(len(thirty['aggregate_columns']), 30)
        self.assertEqual(thirty['aggregate_columns'][0].link.number, '32')
        self.assertEqual(invalid['aggregate_limit'], 10)
        older = build_aggregate_page(self._request('?anchor={}&limit=10'.format(default['aggregate_older_anchor'])), self.game)
        self.assertEqual(older['aggregate_columns'][0].link.number, '22')
        self.assertEqual(older['aggregate_newer_anchor'], default['aggregate_window_anchor'])
        newer = build_aggregate_page(self._request('?anchor={}&limit=10'.format(older['aggregate_newer_anchor'])), self.game)
        self.assertEqual(newer['aggregate_columns'][0].link.number, '32')
        with patch('games.views.track.track_task_change'):
            group = TaskGroup.objects.create(label='newest-after-anchor')
            GameTaskGroup.objects.create(game=self.game, task_group=group, number='33', name='33')
            Task.objects.create(task_group=group, number='1', task_type='default', points=1, checker_data='x', text='x')
        links = list(GameTaskGroup.objects.filter(game=self.game))
        for link in links:
            link.number = str(1000 + link.pk)
            link.save(update_fields=['number'])
        for link in links:
            link.number = str(100000 - link.pk)
            link.save(update_fields=['number'])
        anchored = build_aggregate_page(self._request('?anchor={}&limit=10'.format(default['aggregate_window_anchor'])), self.game)
        self.assertEqual(
            [column.link.pk for column in anchored['aggregate_columns']],
            [column.link.pk for column in default['aggregate_columns']],
        )

    def test_window_scores_denominator_cells_and_tied_rank_ignore_played_count(self):
        user = User.objects.create_user(username='agg-player')
        actor_a = PersonalResultsParticipant(user=user)
        actor_b = PersonalResultsParticipant(anon_key='aggregate-anon')
        at = timezone.now()
        payload = {
            self.links[0][1].pk: [(actor_a, self._info(5, at)), (actor_b, self._info(5, at))],
            self.links[1][1].pk: [(actor_a, self._info(0, at))],
        }
        with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value=payload):
            result = build_aggregate_page(self._request(), self.game)
        rows = {row['actor'].pk: row for row in result['aggregate_rows']}
        self.assertEqual(result['aggregate_window_max'], 20)
        self.assertEqual(rows[actor_a.pk]['score'], 5)
        self.assertEqual(rows[actor_a.pk]['played'], 2)
        self.assertEqual(rows[actor_b.pk]['score'], 5)
        self.assertEqual(rows[actor_b.pk]['played'], 1)
        self.assertEqual(rows[actor_a.pk]['place'], 1)
        self.assertEqual(rows[actor_b.pk]['place'], 1)

    def test_author_is_excluded_only_from_its_release_cell(self):
        user = User.objects.create_user(username='aggregate-author')
        profile = Profile.objects.create(user=user, first_name='Author', last_name='')
        self.links[1][0].task_group.authors.add(profile)
        actor = PersonalResultsParticipant(user=user)
        at = timezone.now()
        payload = {
            self.links[0][1].pk: [(actor, self._info(5, at))],
            self.links[1][1].pk: [(actor, self._info(15, at))],
        }
        with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value=payload):
            result = build_aggregate_page(self._request(), self.game)
        self.assertEqual(len(result['aggregate_rows']), 1)
        row = result['aggregate_rows'][0]
        self.assertEqual(row['score'], 5)
        self.assertEqual(row['played'], 1)
        self.assertIn(self.links[0][0].pk, row['cells'])
        self.assertNotIn(self.links[1][0].pk, row['cells'])

    def test_invalid_anchor_falls_back_to_newest_and_page_is_backend_bounded(self):
        actors = [PersonalResultsParticipant(anon_key='page-{:03}'.format(i)) for i in range(65)]
        payload = {self.links[0][1].pk: [(actor, self._info(5)) for actor in actors]}
        with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value=payload):
            with CaptureQueriesContext(connection) as first_queries:
                first = build_aggregate_page(self._request('?anchor=does-not-exist'), self.game)
            with CaptureQueriesContext(connection) as second_queries:
                second = build_aggregate_page(self._request('?page=2&limit=20'), self.game)
        self.assertEqual(first['aggregate_columns'][0].link.number, '2')
        self.assertEqual(len(first['aggregate_rows']), 50)
        self.assertEqual(second['aggregate_page'].number, 2)
        self.assertEqual(len(second['aggregate_rows']), 15)
        self.assertEqual({r['actor'].pk for r in first['aggregate_rows']} & {r['actor'].pk for r in second['aggregate_rows']}, set())
        self.assertEqual({r['place'] for r in second['aggregate_rows']}, {1})
        self.assertEqual(len(first_queries), len(second_queries))
        # The coverage marker adds one bounded state lookup and the fallback
        # remains backend-bounded regardless of the number of releases.
        self.assertEqual(len(second_queries), 12)

    def test_release_selection_query_count_is_bounded_for_10_and_30(self):
        with patch('games.views.track.track_task_change'):
            for n in range(3, 33):
                group = TaskGroup.objects.create(label='query-aggregate-{}'.format(n))
                GameTaskGroup.objects.create(game=self.game, task_group=group, number=str(n), name=str(n))
                Task.objects.create(task_group=group, number='1', points=1, text='x', checker_data='x')
        with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value={}):
            with CaptureQueriesContext(connection) as ten_queries:
                build_aggregate_page(self._request('?limit=10'), self.game)
            with CaptureQueriesContext(connection) as thirty_queries:
                build_aggregate_page(self._request('?limit=30'), self.game)
        self.assertEqual(len(ten_queries), len(thirty_queries))
        self.assertEqual(len(thirty_queries), 7)

    def test_unknown_max_keeps_release_visible_without_inventing_denominator(self):
        from unittest.mock import patch

        with patch('games.views.track.track_task_change'):
            group = TaskGroup.objects.create(label='unknown-max')
            GameTaskGroup.objects.create(game=self.game, task_group=group, number='3', name='3')
            unknown_task = Task.objects.create(task_group=group, number='1', task_type='default', points=1, checker_data='x', text='x')
        original_get_max = Task.get_results_max_points
        with patch('games.models.Task.get_results_max_points', autospec=True, side_effect=lambda task: None if task.pk == unknown_task.pk else original_get_max(task)):
            with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value={}):
                result = build_aggregate_page(self._request('?limit=10'), self.game)
        self.assertTrue(any(column.max_score is None for column in result['aggregate_columns']))
        self.assertTrue(result['aggregate_has_unknown_max'])
        self.assertEqual(result['aggregate_window_max'], 20)

    def test_aggregate_places_use_competition_rank_for_score_ties(self):
        from games.aggregate_leaderboard import _projection_rank_page

        group = self.links[0][0].task_group
        DailyResultProjectionState.objects.create(
            game=self.game, task_group=group, adapter_version=1,
            coverage_complete=True, is_valid=True, full_refresh_required=False,
        )
        for actor, score in (('rank-a', 100), ('rank-b', 100), ('rank-c', 90)):
            DailyResultProjection.objects.create(
                game=self.game, task_group=group, actor_type='anon',
                actor_key=actor, anon_key=actor, score=score,
            )
        rows, count, page = _projection_rank_page(self.game, [group.pk], 1)
        self.assertEqual(count, 3)
        self.assertEqual(page, 1)
        self.assertEqual([row['place'] for row in rows], [1, 1, 3])

    def test_projection_rank_page_has_exact_count_and_clamps_to_last_page(self):
        from games.aggregate_leaderboard import _projection_rank_page

        group = self.links[0][0].task_group
        DailyResultProjectionState.objects.create(
            game=self.game, task_group=group, adapter_version=1,
            coverage_complete=True, is_valid=True, full_refresh_required=False,
        )
        DailyResultProjection.objects.bulk_create([
            DailyResultProjection(
                game=self.game, task_group=group, actor_type='anon',
                actor_key='page-{:03}'.format(index),
                anon_key='page-{:03}'.format(index), score=100,
            )
            for index in range(51)
        ])

        first, total, page = _projection_rank_page(self.game, [group.pk], 1)
        second, second_total, second_page = _projection_rank_page(self.game, [group.pk], 2)
        beyond, beyond_total, beyond_page = _projection_rank_page(self.game, [group.pk], 99)

        self.assertEqual((len(first), total, page), (50, 51, 1))
        self.assertEqual((len(second), second_total, second_page), (1, 51, 2))
        self.assertEqual((len(beyond), beyond_total, beyond_page), (1, 51, 2))
        self.assertEqual(first[-1]['actor_key'], 'page-049')
        self.assertEqual(second[0]['actor_key'], 'page-050')
        self.assertEqual(beyond[0]['actor_key'], 'page-050')

    def test_projection_rank_page_empty_result_has_zero_count(self):
        from games.aggregate_leaderboard import _projection_rank_page

        group = self.links[0][0].task_group
        DailyResultProjectionState.objects.create(
            game=self.game, task_group=group, adapter_version=1,
            coverage_complete=True, is_valid=True, full_refresh_required=False,
        )
        rows, total, page = _projection_rank_page(self.game, [group.pk], 3)
        self.assertEqual((rows, total, page), ([], 0, 1))

    def test_projection_timing_join_preserves_actor_and_replay_semantics(self):
        from games.aggregate_leaderboard import _projection_rank_page

        group = self.links[0][0].task_group
        team = Team.objects.create(
            name='aggregate-timing-team', project_id='sections', is_hidden=True,
        )
        user = User.objects.create_user(username='aggregate-timing-user')
        actors = [
            ('team', team.name, {'team': team}, 30),
            ('user', str(user.pk), {'user': user}, 20),
            ('anon', 'aggregate-timing-anon', {'anon_key': 'aggregate-timing-anon'}, 10),
            ('anon', 'aggregate-timing-none', {'anon_key': 'aggregate-timing-none'}, 5),
        ]
        DailyResultProjectionState.objects.create(
            game=self.game, task_group=group, adapter_version=1,
            coverage_complete=True, is_valid=True, full_refresh_required=False,
        )
        for actor_type, actor_key, identity, score in actors:
            DailyResultProjection.objects.create(
                game=self.game, task_group=group, actor_type=actor_type,
                actor_key=actor_key, score=score, **identity,
            )
        DailySolveTiming.objects.create(
            game=self.game, task_group=group, team=team,
            accumulated_ms=3000, frozen_ms=3000,
        )
        DailySolveTiming.objects.create(
            game=self.game, task_group=group, user=user,
            accumulated_ms=2000, frozen_ms=2000,
        )
        DailySolveTiming.objects.create(
            game=self.game, task_group=group, anon_key='aggregate-timing-anon',
            accumulated_ms=1000, frozen_ms=1000,
        )
        replay_slot = ReplaySlot.objects.create(
            game=self.game, task_group=group, user=user,
            actor_key='u:{}'.format(user.pk),
        )
        DailySolveTiming.objects.create(
            game=self.game, task_group=group, user=user, replay_slot=replay_slot,
            accumulated_ms=9999, frozen_ms=9999,
        )

        rows, total, page = _projection_rank_page(self.game, [group.pk], 1)
        by_actor = {row['actor_key']: row for row in rows}
        self.assertEqual((total, page), (4, 1))
        self.assertEqual(by_actor[team.name]['total_time_ms'], 3000)
        self.assertEqual(by_actor[str(user.pk)]['total_time_ms'], 2000)
        self.assertEqual(by_actor['aggregate-timing-anon']['total_time_ms'], 1000)
        self.assertIsNone(by_actor['aggregate-timing-none']['total_time_ms'])

    def test_projection_rank_deduplicates_historical_first_play_timings(self):
        from games.aggregate_leaderboard import _projection_rank_page

        user = User.objects.create_user(username='aggregate-duplicate-timing-user')
        groups = [self.links[0][0].task_group]
        for label in ('duplicate-timing-release-2', 'duplicate-timing-release-3'):
            groups.append(TaskGroup.objects.create(label=label))
        for index, group in enumerate(groups[1:], start=3):
            GameTaskGroup.objects.create(
                game=self.game, task_group=group, number=str(index), name=str(index),
            )
        for group, score in zip(groups, (9, 4, 4)):
            DailyResultProjection.objects.create(
                game=self.game, task_group=group, actor_type='user',
                actor_key=str(user.pk), user=user, score=score,
            )

        # Historical MySQL races created two first-play rows.  The completed
        # row is canonical; the running row must not multiply the score.
        now = timezone.now()
        DailySolveTiming.objects.create(
            game=self.game, task_group=groups[0], user=user,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=2000, frozen_ms=2000, completed_at=now,
        )
        DailySolveTiming.objects.create(
            game=self.game, task_group=groups[0], user=user,
            status=DailySolveTiming.STATUS_RUNNING,
            accumulated_ms=0,
        )
        for group, elapsed in ((groups[1], 3000), (groups[2], 4000)):
            DailySolveTiming.objects.create(
                game=self.game, task_group=group, user=user,
                status=DailySolveTiming.STATUS_COMPLETED,
                accumulated_ms=elapsed, frozen_ms=elapsed, completed_at=now,
            )

        rows, total, page = _projection_rank_page(
            self.game, [group.pk for group in groups], 1,
        )
        row = next(item for item in rows if item['actor_key'] == str(user.pk))
        self.assertEqual((total, page), (1, 1))
        self.assertEqual(row['window_score'], 17)
        self.assertEqual(row['played_count'], 3)
        self.assertEqual(row['total_time_ms'], 9000)

    def test_scorer_semantic_version_mismatch_invalidates_projection_read_path(self):
        for link, _task in self.links:
            DailyResultProjectionState.objects.create(
                game=self.game, task_group=link.task_group, adapter_version=1,
                coverage_complete=True, is_valid=True, full_refresh_required=False,
            )
        with patch('games.daily_result_projection.scorer_adapter_version', return_value=2):
            with patch('games.results_sql_aggregate.get_sql_aggregated_game_actor_rows', return_value={}):
                with patch('games.aggregate_leaderboard._projection_rank_page', side_effect=AssertionError('stale projection read')):
                    result = build_aggregate_page(self._request(), self.game)
        self.assertEqual(result['aggregate_rows'], [])
