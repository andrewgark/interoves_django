from datetime import datetime, timedelta
from io import StringIO
import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from games.anon_migrate import claim_and_migrate_anon_history
from games.models import (
    Game,
    GameTaskGroup,
    HTMLPage,
    PlayerCompletedGame,
    PlayerStartedGame,
    Profile,
    Project,
    TaskGroup,
)
from games.product_metrics import (
    MOSCOW,
    TRUSTED_IDENTITY_CUTOVER,
    build_product_metrics_report,
)


def msk(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MOSCOW)


class ProductMetricsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='main', defaults={})
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})
        cls.game = Game.objects.create(
            id='pm_game',
            name='Metrics',
            author='test',
            project_id='main',
            is_ready=True,
        )
        cls.placements = []
        for i in range(24):
            tg = TaskGroup.objects.create(label='pm-tg-{}'.format(i))
            GameTaskGroup.objects.create(
                game=cls.game, task_group=tg, number=str(i + 1), name='P{}'.format(i),
            )
            cls.placements.append((tg, '{}:{}'.format(cls.game.id, tg.pk)))

    def _start(self, at, *, user=None, anon_key=None, placement=0, game_kind='ladder'):
        tg, instance = self.placements[placement]
        row = PlayerStartedGame.objects.create(
            game=self.game,
            task_group=tg,
            game_kind=game_kind,
            game_instance_id=instance,
            user=user,
            anon_key=anon_key,
            instrumentation_version=2,
        )
        PlayerStartedGame.objects.filter(pk=row.pk).update(started_at=at)
        row.refresh_from_db()
        return row

    def _complete(self, at, *, user=None, anon_key=None, placement=0, game_kind='ladder'):
        tg, instance = self.placements[placement]
        row = PlayerCompletedGame.objects.create(
            game=self.game,
            task_group=tg,
            game_kind=game_kind,
            game_instance_id=instance,
            user=user,
            anon_key=anon_key,
            result=PlayerCompletedGame.RESULT_SOLVED,
            instrumentation_version=2,
        )
        PlayerCompletedGame.objects.filter(pk=row.pk).update(completed_at=at)
        row.refresh_from_db()
        return row

    def _report(self, since=None, until=None, **kwargs):
        return build_product_metrics_report(
            since or msk(2026, 9, 16, 0),
            until or msk(2026, 10, 20, 0),
            **kwargs,
        )

    def test_one_anon_many_starts_is_one_player(self):
        self._start(msk(2026, 9, 17), anon_key='a1', placement=0)
        self._start(msk(2026, 9, 18), anon_key='a1', placement=1)
        report = self._report()
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.overview['anonymous_players'], 1)
        self.assertEqual(report.overview['starts'], 2)

    def test_one_user_two_devices_is_one_player(self):
        user = User.objects.create_user('u1', 'u1@example.com', 'x')
        self._start(msk(2026, 9, 17), user=user, placement=0)
        self._start(msk(2026, 9, 18), user=user, placement=1)
        report = self._report()
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.overview['registered_players'], 1)

    def test_claim_does_not_double_count(self):
        user = User.objects.create_user('claim-u', 'claim@example.com', 'x')
        Profile.objects.create(user=user, first_name='C', last_name='U')
        self._start(msk(2026, 9, 17), anon_key='claim-anon', placement=0)
        claim_and_migrate_anon_history(user, 'claim-anon')
        self._start(msk(2026, 9, 18), user=user, placement=1)
        report = self._report()
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.overview['registered_players'], 1)
        self.assertEqual(report.overview['anonymous_players'], 0)
        self.assertEqual(PlayerStartedGame.objects.filter(user=user).count(), 2)
        self.assertFalse(PlayerStartedGame.objects.filter(anon_key='claim-anon').exists())

    def test_two_anon_keys_are_two_players(self):
        self._start(msk(2026, 9, 17), anon_key='a', placement=0)
        self._start(msk(2026, 9, 17), anon_key='b', placement=0)
        report = self._report()
        self.assertEqual(report.overview['players'], 2)

    def test_post_logout_identity_is_separate(self):
        self._start(msk(2026, 9, 17), anon_key='before-logout', placement=0)
        self._start(msk(2026, 9, 18), anon_key='after-logout', placement=0)
        report = self._report()
        self.assertEqual(report.overview['players'], 2)

    def test_new_player_first_start_in_period(self):
        self._start(msk(2026, 9, 17), anon_key='n1', placement=0)
        report = self._report(since=msk(2026, 9, 16, 0), until=msk(2026, 9, 18, 0))
        self.assertEqual(report.overview['new_players'], 1)

    def test_previous_trusted_start_is_not_new(self):
        self._start(msk(2026, 9, 16, 12), anon_key='n2', placement=0)
        self._start(msk(2026, 9, 18, 12), anon_key='n2', placement=1)
        report = self._report(since=msk(2026, 9, 18, 0), until=msk(2026, 9, 19, 0))
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.overview['new_players'], 0)

    def test_several_starts_one_new_player(self):
        self._start(msk(2026, 9, 17, 10), anon_key='n3', placement=0)
        self._start(msk(2026, 9, 17, 15), anon_key='n3', placement=1)
        report = self._report(since=msk(2026, 9, 17, 0), until=msk(2026, 9, 18, 0))
        self.assertEqual(report.overview['new_players'], 1)

    def test_midnight_splits_cohort_and_active_days(self):
        self._start(msk(2026, 9, 20, 23, 30), anon_key='mid', placement=0)
        self._start(msk(2026, 9, 21, 0, 30), anon_key='mid', placement=1)
        report = self._report(since=msk(2026, 9, 20, 0), until=msk(2026, 9, 22, 0))
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.engagement['active_days_distribution'], {'2': 1})
        dates = [row['cohort_date'] for row in report.retention]
        self.assertEqual(dates, ['2026-09-20'])

    def test_five_starts_same_day_one_active_day(self):
        for i in range(5):
            self._start(msk(2026, 9, 17, 10 + i), anon_key='busy', placement=i)
        report = self._report(since=msk(2026, 9, 17, 0), until=msk(2026, 9, 18, 0))
        self.assertEqual(report.engagement['active_days_distribution'], {'1': 1})
        self.assertEqual(report.overview['starts'], 5)

    def test_completion_rate_cohort_by_start(self):
        self._start(msk(2026, 9, 17), anon_key='c1', placement=0)
        self._complete(msk(2026, 9, 17, 13), anon_key='c1', placement=0)
        self._start(msk(2026, 9, 17), anon_key='c2', placement=0)
        self._start(msk(2026, 9, 17), anon_key='c3', placement=0)
        self._complete(msk(2026, 9, 18), anon_key='c3', placement=1)
        report = self._report(since=msk(2026, 9, 17, 0), until=msk(2026, 9, 18, 0))
        rate = report.overview['completion_rate']
        self.assertEqual(rate['started_placements'], 3)
        self.assertEqual(rate['completed_placements'], 1)
        self.assertAlmostEqual(rate['value'], 1 / 3)

    def test_start_before_period_complete_inside_excluded(self):
        self._start(msk(2026, 9, 16, 12), anon_key='late', placement=0)
        self._complete(msk(2026, 9, 17, 12), anon_key='late', placement=0)
        report = self._report(since=msk(2026, 9, 17, 0), until=msk(2026, 9, 18, 0))
        self.assertEqual(report.overview['starts'], 0)
        self.assertEqual(report.overview['completions'], 1)
        self.assertEqual(report.overview['completion_rate']['status'], 'empty')

    def test_complete_after_until_still_counts_for_rate(self):
        self._start(msk(2026, 9, 17, 12), anon_key='later', placement=0)
        self._complete(msk(2026, 9, 19, 12), anon_key='later', placement=0)
        report = self._report(since=msk(2026, 9, 17, 0), until=msk(2026, 9, 18, 0))
        self.assertEqual(report.overview['completion_rate']['completed_placements'], 1)

    def test_exact_d1_yes_and_no(self):
        self._start(msk(2026, 9, 20, 12), anon_key='d1yes', placement=0)
        self._start(msk(2026, 9, 21, 12), anon_key='d1yes', placement=1)
        self._start(msk(2026, 9, 20, 12), anon_key='d1no', placement=0)
        self._start(msk(2026, 9, 22, 12), anon_key='d1no', placement=1)
        report = self._report(since=msk(2026, 9, 20, 0), until=msk(2026, 9, 30, 0))
        row = next(item for item in report.retention if item['cohort_date'] == '2026-09-20')
        self.assertEqual(row['exact_d1']['status'], 'ok')
        self.assertEqual(row['exact_d1']['numerator'], 1)
        self.assertEqual(row['exact_d1']['denominator'], 2)

    def test_exact_d7_vs_rolling(self):
        self._start(msk(2026, 9, 16, 12), anon_key='e7', placement=0)
        self._start(msk(2026, 9, 23, 12), anon_key='e7', placement=1)
        self._start(msk(2026, 9, 16, 12), anon_key='r7', placement=0)
        self._start(msk(2026, 9, 24, 12), anon_key='r7', placement=1)
        report = self._report(since=msk(2026, 9, 16, 0), until=msk(2026, 10, 1, 0))
        row = next(item for item in report.retention if item['cohort_date'] == '2026-09-16')
        self.assertEqual(row['exact_d7']['numerator'], 1)
        self.assertEqual(row['rolling_d7']['numerator'], 2)

    def test_immature_d7_is_not_zero(self):
        self._start(msk(2026, 9, 16, 12), anon_key='imm', placement=0)
        report = self._report(since=msk(2026, 9, 16, 0), until=msk(2026, 9, 18, 0))
        row = report.retention[0]
        self.assertEqual(row['exact_d1']['status'], 'ok')
        self.assertEqual(row['exact_d7']['status'], 'not_yet_observable')
        self.assertIsNone(row['exact_d7']['value'])
        self.assertEqual(row['rolling_d7']['status'], 'not_yet_observable')

    def test_core_boundaries(self):
        until = msk(2026, 10, 16, 0)
        actor = 'core-a'
        for i in range(6):
            self._start(msk(2026, 9, 16) + timedelta(days=i), anon_key=actor, placement=i)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['active_days_7_plus'], 0)
        self._start(msk(2026, 9, 16) + timedelta(days=6), anon_key=actor, placement=6)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['active_days_7_plus'], 1)

        other = 'core-c'
        for i in range(9):
            self._start(msk(2026, 9, 16) + timedelta(days=i), anon_key=other, placement=i)
            self._complete(msk(2026, 9, 16) + timedelta(days=i, hours=1), anon_key=other, placement=i)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['completions_10_plus'], 0)
        self._start(msk(2026, 9, 25), anon_key=other, placement=9)
        self._complete(msk(2026, 9, 25, 1), anon_key=other, placement=9)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['completions_10_plus'], 1)
        self.assertEqual(report.core['completions_20_plus'], 0)

        weeks = 'core-w'
        self._start(msk(2026, 9, 16), anon_key=weeks, placement=10)
        self._start(msk(2026, 9, 23), anon_key=weeks, placement=11)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['active_weeks_3_plus'], 0)
        self._start(msk(2026, 9, 30), anon_key=weeks, placement=12)
        report = build_product_metrics_report(msk(2026, 9, 16, 0), until)
        self.assertEqual(report.core['active_weeks_3_plus'], 1)

    def test_legacy_contaminated_flag(self):
        report = build_product_metrics_report(
            TRUSTED_IDENTITY_CUTOVER - timedelta(days=1),
            msk(2026, 9, 16, 0),
        )
        self.assertTrue(report.legacy_contaminated)
        clean = self._report()
        self.assertFalse(clean.legacy_contaminated)

    def test_game_kind_filter(self):
        self._start(msk(2026, 9, 17), anon_key='k1', placement=0, game_kind='salad')
        self._start(msk(2026, 9, 17), anon_key='k2', placement=0, game_kind='ladder')
        report = self._report(game_kind='salad')
        self.assertEqual(report.overview['players'], 1)
        self.assertEqual(report.overview['starts'], 1)

    def test_independent_cross_check(self):
        self._start(msk(2026, 9, 17, 12), anon_key='x1', placement=0)
        self._start(msk(2026, 9, 17, 13), anon_key='x1', placement=1)
        self._start(msk(2026, 9, 17, 12), user=User.objects.create_user('xu', 'xu@example.com', 'x'), placement=0)
        self._complete(msk(2026, 9, 17, 14), anon_key='x1', placement=0)
        since, until = msk(2026, 9, 17, 0), msk(2026, 9, 18, 0)
        starts = list(
            PlayerStartedGame.objects.filter(started_at__gte=since, started_at__lt=until)
            .values_list('user_id', 'anon_key')
        )
        actors = set()
        for user_id, anon_key in starts:
            actors.add(('user', user_id) if user_id else ('anon', anon_key))
        new_actors = set()
        for actor in actors:
            if actor[0] == 'user':
                first = PlayerStartedGame.objects.filter(
                    user_id=actor[1], started_at__gte=TRUSTED_IDENTITY_CUTOVER,
                ).order_by('started_at').first().started_at
            else:
                first = PlayerStartedGame.objects.filter(
                    anon_key=actor[1], started_at__gte=TRUSTED_IDENTITY_CUTOVER,
                ).order_by('started_at').first().started_at
            if since <= first < until:
                new_actors.add(actor)
        d1_retained = 0
        report = build_product_metrics_report(since, msk(2026, 9, 20, 0))
        self.assertEqual(report.overview['players'], len(actors))
        self.assertEqual(report.overview['starts'], len(starts))
        self.assertEqual(report.overview['completions'], 1)
        self.assertEqual(report.overview['new_players'], len(new_actors))
        row = next(item for item in report.retention if item['cohort_date'] == '2026-09-17')
        self.assertEqual(row['cohort_size'], len(new_actors))
        self.assertEqual(row['exact_d1']['numerator'], d1_retained)

    def test_command_json_and_default_cutover(self):
        self._start(msk(2026, 9, 17), anon_key='cmd', placement=0)
        out = StringIO()
        call_command(
            'product_metrics',
            since='2026-09-16T00:00:00+03:00',
            until='2026-09-18T00:00:00+03:00',
            output_format='json',
            stdout=out,
        )
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['trusted_cutover_sha'], '6c53989')
        self.assertEqual(payload['overview']['players'], 1)
        self.assertFalse(payload['legacy_contaminated'])
        self.assertEqual(payload['timezone'], 'Europe/Moscow')
