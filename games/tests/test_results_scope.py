from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase

from games.community import friends
from games.community.results_scope import FRIENDS, ResultsScope, resolve, scope_options, sql_filter
from games.models import (
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyAggregateSnapshotVersion,
    DailyResultProjection,
    DailyResultProjectionState,
    Game,
    GameTaskGroup,
    HTMLPage,
    PersonalResultsParticipant,
    Profile,
    Project,
    Task,
    TaskGroup,
    Team,
)
from games.results.snapshots.aggregate_snapshot_read import read_snapshot_page
from games.results.standings.aggregate_leaderboard import _projection_rank_page
from games.results.table.presentation import paginate_results_rows


class ScopeFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        Project.objects.get_or_create(pk='sections', defaults={})
        for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.me = User.objects.create_user('sc_me')
        cls.friend = User.objects.create_user('sc_friend')
        cls.stranger = User.objects.create_user('sc_stranger')
        cls.teammate = User.objects.create_user('sc_teammate')
        for user in (cls.me, cls.friend, cls.stranger, cls.teammate):
            Profile.objects.create(user=user, first_name=user.username, last_name='X')
        cls.team = Team.objects.create(name='sc_team', visible_name='Сфинксы')
        cls.other_team = Team.objects.create(name='sc_other', visible_name='Ёжики')
        cls.me.profile.add_team_membership(cls.team, make_primary=True)
        cls.teammate.profile.add_team_membership(cls.team, make_primary=True)
        friends.request_friendship(cls.me, cls.friend.pk)
        friends.accept_request(cls.friend, cls.me.pk)
        # Scores give places: stranger 1, other_team 2, friend 3, team 4, me 5, teammate 6.
        cls.actors = [
            ('user', str(cls.stranger.pk), {'user': cls.stranger}, 60),
            ('team', cls.other_team.pk, {'team': cls.other_team}, 50),
            ('user', str(cls.friend.pk), {'user': cls.friend}, 40),
            ('team', cls.team.pk, {'team': cls.team}, 30),
            ('user', str(cls.me.pk), {'user': cls.me}, 20),
            ('user', str(cls.teammate.pk), {'user': cls.teammate}, 10),
        ]

    def request(self, query='', user=None, cookies=None):
        request = RequestFactory().get('/ladder/results/' + query)
        request.user = user or self.me
        request.session = {}
        if cookies:
            request.COOKIES.update(cookies)
        return request


class ResolveScopeTests(ScopeFixture):
    def test_friends_scope_includes_me_my_active_team_and_friends(self):
        scope = resolve(self.request('?scope=friends'))
        self.assertEqual(scope.identities, {
            ('user', str(self.me.pk)), ('user', str(self.friend.pk)), ('team', self.team.pk),
        })

    def test_team_scope_includes_team_row_and_members(self):
        scope = resolve(self.request('?scope=team:' + self.team.public_id))
        self.assertEqual(scope.identities, {
            ('team', self.team.pk), ('user', str(self.me.pk)), ('user', str(self.teammate.pk)),
        })

    def test_foreign_team_anonymous_and_unknown_scopes_are_ignored(self):
        self.assertIsNone(resolve(self.request('?scope=team:' + self.other_team.public_id)))
        self.assertIsNone(resolve(self.request('?scope=friends', user=AnonymousUser())))
        self.assertIsNone(resolve(self.request('?scope=whatever')))

    def test_team_scope_survives_the_encoded_cookie(self):
        cookie = 'team%3A' + self.team.public_id  # what encodeURIComponent stores
        self.assertEqual(resolve(self.request(cookies={'results_scope': cookie})).key, 'team:' + self.team.public_id)

    def test_cookie_applies_without_param_and_all_overrides_it(self):
        self.assertEqual(resolve(self.request(cookies={'results_scope': 'friends'})).key, FRIENDS)
        self.assertIsNone(resolve(self.request('?scope=all', cookies={'results_scope': 'friends'})))

    def test_options_list_all_friends_and_my_teams(self):
        options = scope_options(self.request('?scope=friends'), lambda value: '/x/?scope=' + value)
        self.assertEqual([o['label'] for o in options], ['Все', 'Друзья', 'Сфинксы'])
        self.assertEqual([o['active'] for o in options], [False, True, False])
        self.assertEqual(scope_options(self.request(user=AnonymousUser()), str), [])

    def test_sql_filter_groups_by_type(self):
        sql, params = sql_filter(ResultsScope('x', 'x', frozenset({('user', '2'), ('user', '1'), ('team', 't')})))
        self.assertEqual(sql, '((actor_type = %s AND actor_key IN (%s)) OR (actor_type = %s AND actor_key IN (%s, %s)))')
        self.assertEqual(params, ['team', 't', 'user', '1', '2'])
        self.assertEqual(sql_filter(ResultsScope('x', 'x', frozenset())), ('1 = 0', []))


class ScopedTablesKeepGlobalPlacesTests(ScopeFixture):
    def test_saved_monthly_table(self):
        game = Game.objects.create(
            id='sc_month', name='Scope', author='t', author_extra='', project_id='sections', is_ready=True,
        )
        snapshot = DailyAggregateSnapshot.objects.create(game=game, month=date(2026, 11, 1))
        version = DailyAggregateSnapshotVersion.objects.create(snapshot=snapshot, columns=[], source_versions={})
        for actor_type, key, extra, score in self.actors:
            DailyAggregateSnapshotRow.objects.create(
                version=version, actor_type=actor_type, actor_key=key, score=Decimal(score),
                played_count=1, cells={}, **extra,
            )
        all_types = {'user', 'team', 'anon'}
        scope = resolve(self.request('?scope=team:' + self.team.public_id))
        rows, total, page, _, view = read_snapshot_page(version, 1, all_types, 'time', scope=scope)
        self.assertEqual(total, 3)
        self.assertEqual([(r['actor_key'], r['place']) for r in rows], [
            (self.team.pk, 4), (str(self.me.pk), 5), (str(self.teammate.pk), 6),
        ])
        *_, unscoped_view = read_snapshot_page(version, 1, all_types, 'time')
        self.assertNotEqual(view, unscoped_view)

    def test_projection_ranking(self):
        game = Game.objects.create(
            id='sc_proj', name='Scope', author='t', author_extra='', project_id='sections', is_ready=True,
        )
        with patch('games.realtime.track_compat.track_task_change'):
            group = TaskGroup.objects.create(label='sc_group')
            GameTaskGroup.objects.create(game=game, task_group=group, number='1', name='1')
            Task.objects.create(task_group=group, number='1', task_type='default', points=100, checker_data='x', text='x')
        DailyResultProjectionState.objects.create(
            game=game, task_group=group, adapter_version=1,
            coverage_complete=True, is_valid=True, full_refresh_required=False,
        )
        for actor_type, key, extra, score in self.actors:
            DailyResultProjection.objects.create(
                game=game, task_group=group, actor_type=actor_type, actor_key=key, score=score, **extra,
            )
        scope = resolve(self.request('?scope=friends'))
        rows, total, _ = _projection_rank_page(game, [group.pk], 1, scope=scope)
        self.assertEqual(total, 3)
        self.assertEqual(
            [(r['user_id'] or r['team_id'], r['place']) for r in rows],
            [(self.friend.pk, 3), (self.team.pk, 4), (self.me.pk, 5)],
        )

    def test_release_table_rows(self):
        actors = [
            PersonalResultsParticipant(user_id=self.stranger.pk),
            self.other_team,
            PersonalResultsParticipant(user_id=self.friend.pk),
            self.team,
            PersonalResultsParticipant(user_id=self.me.pk),
            PersonalResultsParticipant(anon_key='anon-1'),
        ]
        places = {actor: index + 1 for index, actor in enumerate(actors)}
        out = paginate_results_rows(
            self.request('?scope=friends'), {'teams_sorted': actors, 'team_to_place': places},
        )
        self.assertEqual(out['teams_sorted'], [actors[2], actors[3], actors[4]])
        self.assertEqual([out['team_to_place'][a] for a in out['teams_sorted']], [3, 4, 5])
        self.assertTrue(out['results_scope_active'])
        self.assertEqual(out['page_total_rows'], 3)
