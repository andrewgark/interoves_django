from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import json

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase

from games.daily_section import is_daily_timing_game
from games.daily_timing import (
    ACTION_AUTO_PAUSE,
    ACTION_COMPLETE,
    ACTION_HEARTBEAT,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START,
    HEARTBEAT_MAX_CREDIT_MS,
    apply_timing_event,
    canonical_elapsed_seconds,
    complete_daily_timing,
    lookup_timing,
    merge_timing_rows,
)
from games.models import (
    CheckerType,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    Task,
    TaskGroup,
)
from games.share_result import elapsed_seconds_from_attempts as share_elapsed
from games.word_salad import WORD_SALAD_GAME_ID


def _dt(seconds=0):
    return datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc) + timedelta(seconds=seconds)


class DailyTimingScopeTests(SimpleTestCase):
    def test_only_official_daily_games(self):
        self.assertTrue(is_daily_timing_game('ladder'))
        self.assertTrue(is_daily_timing_game('alphabetty'))
        self.assertTrue(is_daily_timing_game(WORD_SALAD_GAME_ID))
        self.assertFalse(is_daily_timing_game('week_task'))
        self.assertFalse(is_daily_timing_game('des1'))
        self.assertFalse(is_daily_timing_game('walls'))


class DailyTimingDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        HTMLPage.objects.get_or_create(name='Правила Десяточки', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила турнирного режима', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила тренировочного режима', defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        cls.game = Game.objects.filter(id='ladder', project_id='sections').first()
        if cls.game is None:
            cls.game = Game.objects.create(
                id='ladder',
                name='Лесенка',
                author='t',
                project_id='sections',
                is_ready=True,
            )
        cls.tg = TaskGroup.objects.create(label='daily-timing-tg')
        cls.link = GameTaskGroup.objects.create(
            game=cls.game, task_group=cls.tg, number='91001', name='T',
        )
        cls.task = Task.objects.create(
            task_group=cls.tg, number='1', checker=CheckerType.objects.get(pk='equals'),
            points=1, answer='ok',
        )
        cls.user = User.objects.create_user('timing_user', 't@example.com', 'secret')
        Profile.objects.create(user=cls.user, first_name='T', last_name='U')
        cls.anon = 'anon-daily-timing-1'

    def _apply(self, *, action, session, seq, event=None, claimed=None, now=None, user='user', create=True):
        kwargs = {
            'game': self.game,
            'task_group': self.tg,
            'action': action,
            'session_id': session,
            'event_id': event or '{}-{}'.format(action, seq),
            'seq': seq,
            'claimed_ms': claimed,
            'now': now or _dt(),
            'create': create,
        }
        if user == 'user':
            kwargs['user'] = self.user
        else:
            kwargs['anon_key'] = self.anon
        return apply_timing_event(**kwargs)

    def test_continuous_solve_accumulates_from_server_clock(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = self._apply(
            action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15),
        )
        self.assertEqual(snap['status'], 'running')
        self.assertEqual(snap['committed_ms'], 15000)
        snap = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(20),
        )
        self.assertTrue(snap['completed'])
        self.assertEqual(snap['frozen_ms'], 20000)

    def test_hidden_tab_does_not_keep_counting(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=8000, now=_dt(8))
        later = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=3, claimed=999999, now=_dt(3600))
        self.assertFalse(later['is_authoritative'])
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 8000)
        self.assertEqual(row.status, DailySolveTiming.STATUS_AUTO_PAUSED)

    def test_return_to_tab_starts_new_interval(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=5000, now=_dt(5))
        self._apply(action=ACTION_START, session=sid, seq=3, now=_dt(3600))
        snap = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=4, claimed=10000, now=_dt(3610))
        self.assertEqual(snap['committed_ms'], 15000)

    def test_manual_pause_survives_reload_and_visibility(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_PAUSE, session=sid, seq=2, claimed=4000, now=_dt(4))
        start = self._apply(action=ACTION_START, session=sid, seq=3, now=_dt(10))
        self.assertEqual(start['status'], 'manually_paused')
        self.assertFalse(start['is_authoritative'])
        resumed = self._apply(action=ACTION_RESUME, session=sid, seq=4, now=_dt(12))
        self.assertEqual(resumed['status'], 'running')
        self.assertTrue(resumed['is_authoritative'])

    def test_duplicate_heartbeat_is_idempotent(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        first = self._apply(
            action=ACTION_HEARTBEAT, session=sid, seq=2, event='hb-2', claimed=15000, now=_dt(15),
        )
        dup = self._apply(
            action=ACTION_HEARTBEAT, session=sid, seq=2, event='hb-2', claimed=15000, now=_dt(15),
        )
        self.assertEqual(first['committed_ms'], dup['committed_ms'])
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 15000)

    def test_out_of_order_seq_is_ignored(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=4, claimed=15000, now=_dt(15))
        stale = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=3, claimed=15000, now=_dt(30))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 15000)
        self.assertEqual(stale['committed_ms'], 15000)

    def test_two_tabs_do_not_double_count(self):
        a = uuid4()
        b = uuid4()
        self._apply(action=ACTION_START, session=a, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=a, seq=2, claimed=15000, now=_dt(15))
        takeover = self._apply(action=ACTION_START, session=b, seq=3, now=_dt(20))
        self.assertTrue(takeover['is_authoritative'])
        ghost = self._apply(action=ACTION_HEARTBEAT, session=a, seq=4, claimed=15000, now=_dt(35))
        self.assertFalse(ghost['is_authoritative'])
        live = self._apply(action=ACTION_HEARTBEAT, session=b, seq=5, claimed=10000, now=_dt(30))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertLess(row.accumulated_ms, 40000)
        self.assertGreaterEqual(row.accumulated_ms, 20000)
        self.assertEqual(live['committed_ms'], row.accumulated_ms)

    def test_second_tab_can_takeover_with_its_own_seq(self):
        a = uuid4()
        b = uuid4()
        self._apply(action=ACTION_START, session=a, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=a, seq=2, claimed=15000, now=_dt(15))
        takeover = self._apply(
            action=ACTION_START, session=b, seq=1, event='start-b', now=_dt(20),
        )
        self.assertTrue(takeover['is_authoritative'])
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.active_session_id, b)

    def test_foreign_auto_pause_does_not_kill_live_lease(self):
        a = uuid4()
        b = uuid4()
        self._apply(action=ACTION_START, session=a, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=b, seq=1, event='pause-b', claimed=0, now=_dt(3))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.status, DailySolveTiming.STATUS_RUNNING)
        self.assertEqual(row.active_session_id, a)
        self._apply(action=ACTION_HEARTBEAT, session=a, seq=2, claimed=10000, now=_dt(10))
        row.refresh_from_db()
        self.assertEqual(row.accumulated_ms, 10000)

    def test_complete_without_row_keeps_legacy_formula(self):
        snap = complete_daily_timing(game=self.game, task_group=self.tg, user=self.user, now=_dt(12))
        self.assertIsNone(snap)
        self.assertFalse(
            DailySolveTiming.objects.filter(game=self.game, task_group=self.tg, user=self.user).exists()
        )
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=226)),
        ]
        self.assertEqual(
            canonical_elapsed_seconds(
                game=self.game, task_group=self.tg, user=self.user, attempts=attempts,
            ),
            226,
        )

    def test_overlapping_devices_use_lease(self):
        phone = uuid4()
        laptop = uuid4()
        self._apply(action=ACTION_START, session=laptop, seq=1, now=_dt())
        self._apply(action=ACTION_START, session=phone, seq=2, now=_dt(5))
        self._apply(action=ACTION_HEARTBEAT, session=phone, seq=3, claimed=10000, now=_dt(15))
        self._apply(action=ACTION_HEARTBEAT, session=laptop, seq=4, claimed=60000, now=_dt(65))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertLessEqual(row.accumulated_ms, 20000)

    def test_huge_claimed_delta_is_capped(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = self._apply(
            action=ACTION_HEARTBEAT,
            session=sid,
            seq=2,
            claimed=86_400_000,
            now=_dt(10),
        )
        self.assertEqual(snap['committed_ms'], 10000)

    def test_stale_request_after_completion_does_not_increase_time(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        complete_daily_timing(game=self.game, task_group=self.tg, user=self.user, now=_dt(12))
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=9, claimed=50000, now=_dt(80))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.status, DailySolveTiming.STATUS_COMPLETED)
        self.assertEqual(row.frozen_ms, 12000)

    def test_anonymous_and_authenticated_are_separate_rows(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, user='anon', now=_dt())
        self._apply(action=ACTION_START, session=uuid4(), seq=1, user='user', now=_dt())
        self.assertEqual(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).count(), 2)

    def test_legacy_completed_uses_first_to_last(self):
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=226)),
        ]
        self.assertEqual(
            canonical_elapsed_seconds(
                game=self.game, task_group=self.tg, user=self.user, attempts=attempts,
            ),
            226,
        )

    def test_v1_completed_uses_frozen_ms_not_attempts(self):
        DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            timing_version=1,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=390000,
            frozen_ms=390000,
            completed_at=_dt(10),
        )
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(hours=3)),
        ]
        self.assertEqual(
            canonical_elapsed_seconds(
                game=self.game, task_group=self.tg, user=self.user, attempts=attempts,
            ),
            390,
        )

    def test_non_daily_game_keeps_first_to_last(self):
        other = Game.objects.create(
            id='timing_other_game',
            name='Other',
            author='t',
            project_id='sections',
            is_ready=True,
        )
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=50)),
        ]
        self.assertEqual(
            canonical_elapsed_seconds(game=other, task_group=self.tg, user=self.user, attempts=attempts),
            50,
        )
        self.assertEqual(share_elapsed(attempts), 50)

    def test_team_actor_stays_on_legacy_formula(self):
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=90)),
        ]
        self.assertEqual(
            canonical_elapsed_seconds(
                game=self.game,
                task_group=self.tg,
                user=self.user,
                team=SimpleNamespace(name='x'),
                attempts=attempts,
            ),
            90,
        )

    def test_crash_without_flush_caps_to_heartbeat_window(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15))
        # Tab died; hours later another session takeovers using last heartbeat, not wall clock.
        self._apply(action=ACTION_START, session=uuid4(), seq=3, now=_dt(3 * 3600))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertLessEqual(row.accumulated_ms, 15000 + HEARTBEAT_MAX_CREDIT_MS)

    def test_pause_credits_open_interval_from_claimed(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15))
        snap = self._apply(
            action=ACTION_PAUSE, session=sid, seq=3, claimed=260000, now=_dt(15 + 260),
        )
        self.assertEqual(snap['status'], 'manually_paused')
        self.assertEqual(snap['committed_ms'], 15000 + 260000)

    def test_auto_pause_credits_claimed_open_interval(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = self._apply(
            action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=90000, now=_dt(90),
        )
        self.assertEqual(snap['status'], 'auto_paused')
        self.assertEqual(snap['committed_ms'], 90000)

    def test_complete_credits_open_interval_beyond_heartbeat_cap(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15))
        snap = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(15 + 90),
        )
        self.assertEqual(snap['frozen_ms'], 15000 + 90000)

    def test_start_create_race_reuses_existing_row(self):
        from django.db import IntegrityError

        existing = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_AUTO_PAUSED,
        )
        seen = {'first': 0}
        orig_sfu = DailySolveTiming.objects.select_for_update

        def sfu_wrapper(*args, **kwargs):
            qs = orig_sfu(*args, **kwargs)
            orig_filter = qs.filter

            def filter_wrapper(*fargs, **fkwargs):
                fqs = orig_filter(*fargs, **fkwargs)
                orig_first = fqs.first

                def first_wrapper():
                    seen['first'] += 1
                    if seen['first'] == 1:
                        return None
                    return orig_first()

                fqs.first = first_wrapper
                return fqs

            qs.filter = filter_wrapper
            return qs

        sid = uuid4()
        with patch.object(DailySolveTiming.objects, 'select_for_update', side_effect=sfu_wrapper):
            with patch.object(DailySolveTiming.objects, 'create', side_effect=IntegrityError('uniq')):
                snap = self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self.assertTrue(snap['exists'])
        self.assertEqual(snap['status'], 'running')
        self.assertEqual(DailySolveTiming.objects.filter(pk=existing.pk).count(), 1)

    def test_merge_prefers_completed_and_does_not_sum(self):
        target = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=12000,
            frozen_ms=12000,
        )
        source_user = User.objects.create_user('timing_src', 's@example.com', 'secret')
        source = DailySolveTiming.objects.create(
            user=source_user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            accumulated_ms=8000,
        )
        merge_timing_rows(target, source)
        target.refresh_from_db()
        self.assertEqual(target.frozen_ms, 12000)
        self.assertFalse(DailySolveTiming.objects.filter(pk=source.pk).exists())


class DailyTimingApiTests(TestCase):
    def setUp(self):
        self.game = Game.objects.filter(id='ladder', project_id='sections').first()
        self.assertIsNotNone(self.game)
        self.tg = TaskGroup.objects.create(label='daily-timing-api')
        self.link = GameTaskGroup.objects.create(
            game=self.game, task_group=self.tg, number='91002', name='API',
        )
        self.anon = 'anon-timing-api'
        self.client = Client()

    def _post(self, payload):
        import json
        return self.client.post(
            '/ladder/91002/timing/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=self.anon,
        )

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_anonymous_start_and_pause(self, _pub):
        resp = self._post({
            'action': ACTION_START,
            'session_id': str(uuid4()),
            'event_id': 'e1',
            'seq': 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'running')
        self.assertTrue(data['is_authoritative'])

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_authenticated_start(self, _pub):
        user = User.objects.create_user('timing_api_user', 'api@example.com', 'secret')
        Profile.objects.create(user=user, first_name='A', last_name='P')
        self.client.force_login(user)
        resp = self.client.post(
            '/ladder/91002/timing/',
            data=json.dumps({
                'action': ACTION_START,
                'session_id': str(uuid4()),
                'event_id': 'auth-1',
                'seq': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'running')
        self.assertTrue(
            DailySolveTiming.objects.filter(user=user, game=self.game, task_group=self.link.task_group).exists()
        )

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_unknown_action_is_not_ok(self, _pub):
        resp = self._post({
            'action': 'explode',
            'session_id': str(uuid4()),
            'event_id': 'bad',
            'seq': 1,
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['error'], 'bad_action')

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_get_with_session_id_is_authoritative(self, _pub):
        sid = str(uuid4())
        start = self._post({
            'action': ACTION_START,
            'session_id': sid,
            'event_id': 'g1',
            'seq': 1,
        })
        self.assertTrue(start.json()['is_authoritative'])
        resp = self.client.get(
            '/ladder/91002/timing/?session_id={}'.format(sid),
            HTTP_X_INTEROVES_ANON=self.anon,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'running')
        self.assertTrue(data['is_authoritative'])

    def test_week_task_has_no_timing_route_semantics(self):
        self.assertFalse(is_daily_timing_game('week_task'))

    def test_non_daily_url_404(self):
        resp = self.client.post(
            '/walls/1/timing/',
            data=json.dumps({'action': 'start', 'session_id': str(uuid4()), 'event_id': 'x', 'seq': 1}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=self.anon,
        )
        self.assertEqual(resp.status_code, 404)
