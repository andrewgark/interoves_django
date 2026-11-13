from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from games.models import (
    DailyAggregateSnapshot,
    DailyAggregateSnapshotRow,
    DailyAggregateSnapshotVersion,
    DailyResultProjection,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    TaskGroup,
    Team,
)
from games.results.snapshots.aggregate_snapshot_read import read_snapshot_page, snapshot_actor_place
from games.results.snapshots.aggregate_snapshots import (
    _authored_groups,
    _eligible_count,
    _prepublication_groups,
)
from games.results.standings.aggregate_leaderboard import _average_display, _projection_rank_page

ALL = {'user', 'team', 'anon'}


def make_game(game_id):
    Project.objects.get_or_create(pk='main', defaults={})
    for name in ('Правила Десяточки', 'Правила турнирного режима', 'Правила тренировочного режима'):
        HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
    return Game.objects.create(id=game_id, name=game_id, author='t', author_extra='', project_id='main')


class MonthlyAverageRankingTests(TestCase):
    """Monthly tables rank by averages over the releases an actor could play."""

    @classmethod
    def setUpTestData(cls):
        cls.game = make_game('avg_test')
        snapshot = DailyAggregateSnapshot.objects.create(game=cls.game, month=date(2026, 11, 1))
        cls.version = DailyAggregateSnapshotVersion.objects.create(snapshot=snapshot, columns=[], source_versions={})
        cls.users = {}
        for name in ('author', 'steady', 'sprinter', 'slow'):
            user = User.objects.create_user('avg_' + name)
            Profile.objects.create(user=user, first_name=name, last_name='')
            cls.users[name] = user

    def row(self, name, score, *, eligible, played, time_ms=None, timed=0, attempts=0):
        user = self.users[name]
        DailyAggregateSnapshotRow.objects.create(
            version=self.version, actor_type='user', actor_key=str(user.pk), user=user,
            score=Decimal(score), eligible_count=eligible, played_count=played,
            total_time_ms=time_ms, timed_count=timed, attempts_count=attempts, cells={},
        )

    def order(self, sort='time'):
        rows, *_ = read_snapshot_page(self.version, 1, ALL, sort)
        return [(row['user_id'], row['place']) for row in rows]

    def test_author_is_not_punished_for_own_release(self):
        # 4 releases; the author wrote one and solved the other three fully.
        self.row('author', 30, eligible=3, played=3)
        self.row('steady', 36, eligible=4, played=4)  # 9 per release
        rows, *_ = read_snapshot_page(self.version, 1, ALL, 'time')
        self.assertEqual([r['user_id'] for r in rows], [self.users['author'].pk, self.users['steady'].pk])
        self.assertEqual(float(rows[0]['window_score']), 10.0)
        self.assertEqual(float(rows[0]['total_score']), 30.0)

    def test_skipped_release_counts_as_zero(self):
        self.row('sprinter', 10, eligible=4, played=1)  # one perfect release
        self.row('steady', 20, eligible=4, played=4)
        self.assertEqual(self.order()[0][0], self.users['steady'].pk)

    def test_tiebreak_uses_average_time_not_total(self):
        # Same average score; the one who played more has more total time
        # but is faster per release.
        self.row('steady', 40, eligible=4, played=4, time_ms=400_000, timed=4)
        self.row('slow', 40, eligible=4, played=4, time_ms=480_000, timed=3)
        self.assertEqual(self.order(), [(self.users['steady'].pk, 1), (self.users['slow'].pk, 1)])
        place = snapshot_actor_place(self.version.pk, 'user', self.users['slow'].pk, 'time')
        self.assertEqual(place, {'place': 1, 'place_tie': 2, 'total': 2})

    def test_attempts_tiebreak_is_per_release(self):
        self.row('steady', 40, eligible=4, played=4, attempts=20)  # 5 per release
        self.row('slow', 30, eligible=3, played=3, attempts=18)  # 6 per release
        self.assertEqual(self.order('attempts')[0][0], self.users['steady'].pk)

    def test_rows_from_versions_before_averages_keep_sums(self):
        self.row('steady', 36, eligible=0, played=4)
        self.row('author', 30, eligible=0, played=3)
        rows, *_ = read_snapshot_page(self.version, 1, ALL, 'time')
        self.assertEqual(float(rows[0]['window_score']), 36.0)

    def test_hidden_actor_has_no_place(self):
        self.row('steady', 36, eligible=4, played=4)
        Profile.objects.filter(user=self.users['steady']).update(is_hidden=True)
        self.assertIsNone(snapshot_actor_place(self.version.pk, 'user', self.users['steady'].pk, 'time'))


class AuthoredReleaseCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user('ac_author')
        cls.profile = Profile.objects.create(user=cls.author, first_name='A', last_name='B')
        cls.team = Team.objects.create(name='ac_team')
        cls.other_team = Team.objects.create(name='ac_other')
        cls.profile.add_team_membership(cls.team, make_primary=True)
        cls.groups = [TaskGroup.objects.create(label='ac-{}'.format(i)) for i in range(3)]
        cls.groups[1].authors.add(cls.profile)

    def test_user_and_their_teams_lose_only_written_releases(self):
        ids = [group.pk for group in self.groups]
        authored = _authored_groups(ids)
        count = len(ids)
        self.assertEqual(_eligible_count({'actor_type': 'user', 'user_id': self.author.pk}, count, authored), 2)
        self.assertEqual(_eligible_count({'actor_type': 'team', 'team_id': self.team.pk}, count, authored), 2)
        self.assertEqual(_eligible_count({'actor_type': 'team', 'team_id': self.other_team.pk}, count, authored), 3)
        self.assertEqual(_eligible_count({'actor_type': 'anon', 'anon_key': 'x'}, count, authored), 3)


class SnapshotBuildTimedCountTests(TestCase):
    def test_build_query_counts_releases_with_time(self):
        game = make_game('timed_test')
        user = User.objects.create_user('timed_user')
        Profile.objects.create(user=user, first_name='T', last_name='U')
        groups = []
        with patch('games.realtime.track_compat.track_task_change'):
            for number in (1, 2, 3):
                group = TaskGroup.objects.create(label='timed-{}'.format(number))
                GameTaskGroup.objects.create(game=game, task_group=group, number=str(number), name=str(number))
                groups.append(group)
        for index, group in enumerate(groups):
            DailyResultProjection.objects.create(
                game=game, task_group=group, actor_type='user', actor_key=str(user.pk),
                user=user, score=5, fallback_duration_seconds=30 if index == 1 else 0,
            )
        DailySolveTiming.objects.create(
            game=game, task_group=groups[0], user=user, status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=60_000, frozen_ms=60_000, timing_version=DailySolveTiming.TIMING_VERSION_ACTIVE,
        )
        rows, total, _ = _projection_rank_page(game, [g.pk for g in groups], 1, snapshot_build=True)
        self.assertEqual(total, 1)
        # Release 1 has a timer, release 2 a fallback duration, release 3 nothing.
        self.assertEqual(int(rows[0]['timed_count']), 2)
        self.assertEqual(float(rows[0]['total_time_ms']), 90_000)


class PrepublicationAndDisplayTests(TestCase):
    def test_tested_release_is_not_a_skipped_one(self):
        game = make_game('prepub_test')
        tester = User.objects.create_user('pp_tester')
        Profile.objects.create(user=tester, first_name='T', last_name='T')
        groups = [TaskGroup.objects.create(label='pp-{}'.format(i)) for i in range(3)]
        groups[0].authors.add(tester.profile)
        for group, pre in ((groups[0], True), (groups[1], True), (groups[2], False)):
            DailyResultProjection.objects.create(
                game=game, task_group=group, actor_type='user', actor_key=str(tester.pk),
                user=tester, score=5, is_prepublication=pre,
            )
        ids = [g.pk for g in groups]
        row = {'actor_type': 'user', 'actor_key': str(tester.pk), 'user_id': tester.pk}
        # Wrote #0 (and tested it), tested #1: only #2 counts.
        self.assertEqual(_eligible_count(row, 3, _authored_groups(ids), _prepublication_groups(game, ids)), 1)

    def test_average_attempts_read_without_trailing_zero(self):
        self.assertEqual(_average_display(Decimal('19.000')), 19)
        self.assertEqual(_average_display(19.333), 19.3)
        self.assertIsNone(_average_display(None))
