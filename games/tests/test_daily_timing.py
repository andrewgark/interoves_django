from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import json

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, transaction
from django.test import Client, SimpleTestCase, TestCase
from django.urls import resolve

from games.analytics_identity import attach_anon_cookie
from games import daily_timing as daily_timing_mod
from games.daily_section import is_daily_team_timing_game, is_daily_timing_game
from games.daily_timing import (
    ACTION_AUTO_PAUSE,
    ACTION_COMPLETE,
    ACTION_HEARTBEAT,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START,
    HEARTBEAT_MAX_CREDIT_MS,
    TIMING_DEADLOCK_ATTEMPTS,
    _is_mysql_deadlock,
    apply_timing_event,
    canonical_elapsed_seconds,
    complete_daily_timing,
    lookup_timing,
    merge_timing_rows,
)
from games.views.daily_timing_views import daily_timing_page_context
from games.models import (
    CheckerType,
    DailySolveTiming,
    Game,
    GameTaskGroup,
    HTMLPage,
    Profile,
    Project,
    ReplaySlot,
    Task,
    TaskGroup,
    Team,
)
from games.share_result import elapsed_seconds_from_attempts as share_elapsed
from games.word_salad import WORD_SALAD_GAME_ID


def _dt(seconds=0):
    return datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc) + timedelta(seconds=seconds)


class DailyTimingScopeTests(SimpleTestCase):
    def test_all_public_section_games_use_authoritative_active_timing(self):
        for game_id in ('ladder', 'salad', 'alphabetty', 'replacements', 'walls', 'palindromes', 'week_task'):
            self.assertTrue(is_daily_timing_game(game_id), game_id)
        self.assertFalse(is_daily_team_timing_game('alphabetty'))
        for game_id in ('ladder', 'salad', 'replacements', 'walls', 'palindromes', 'week_task'):
            self.assertTrue(is_daily_team_timing_game(game_id), game_id)
        self.assertFalse(is_daily_timing_game('des1'))

    def test_new_section_game_timing_routes_resolve_to_shared_endpoint(self):
        for game_id in ('replacements', 'walls', 'palindromes', 'week_task'):
            match = resolve('/{}/123/timing/'.format(game_id))
            self.assertEqual(match.func.__name__, 'daily_solve_timing', game_id)

    def test_detects_wrapped_mysql_deadlock_only(self):
        inner = OperationalError(1213, 'Deadlock found when trying to get lock')
        wrapped = OperationalError('Deadlock found when trying to get lock')
        wrapped.__cause__ = inner
        self.assertTrue(_is_mysql_deadlock(inner))
        self.assertTrue(_is_mysql_deadlock(wrapped))
        self.assertFalse(
            _is_mysql_deadlock(OperationalError(1205, 'Lock wait timeout exceeded'))
        )
        self.assertFalse(_is_mysql_deadlock(IntegrityError(1062, 'Duplicate entry')))

    def test_replays_do_not_enable_the_official_timer(self):
        context = daily_timing_page_context(
            None,
            SimpleNamespace(id=WORD_SALAD_GAME_ID),
            SimpleNamespace(number='26', task_group=SimpleNamespace()),
            replay_slot=SimpleNamespace(),
        )
        self.assertFalse(context['daily_timing_enabled'])
        self.assertEqual(context['daily_timing_url'], '')

    def test_completed_official_game_does_not_enable_the_timer(self):
        context = daily_timing_page_context(
            None,
            SimpleNamespace(id=WORD_SALAD_GAME_ID),
            SimpleNamespace(number='26', task_group=SimpleNamespace()),
            official_completed=True,
        )
        self.assertFalse(context['daily_timing_enabled'])
        self.assertEqual(context['daily_timing_url'], '')


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

    def test_database_constraints_enforce_team_actor_shape_and_play_uniqueness(self):
        team = Team.objects.create(name='timing-constraint-team', project_id='sections')
        first = DailySolveTiming.objects.create(
            team=team, game=self.game, task_group=self.tg,
            team_timing_key='first',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DailySolveTiming.objects.create(
                    team=team, game=self.game, task_group=self.tg,
                    team_timing_key='first',
                )

        replay = ReplaySlot.objects.create(
            actor_key='team:{}'.format(team.pk), team=team, game=self.game,
            task_group=self.tg,
        )
        replay_row = DailySolveTiming.objects.create(
            team=team, game=self.game, task_group=self.tg, replay_slot=replay,
            team_timing_key='replay:{}'.format(replay.pk),
        )
        self.assertNotEqual(first.pk, replay_row.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DailySolveTiming.objects.create(
                    team=team, user=self.user, game=self.game, task_group=self.tg,
                    team_timing_key='invalid-actor',
                )

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

    def test_existing_legacy_attempt_cannot_receive_a_late_guessed_start(self):
        from games.models import Attempt

        Attempt.manager.create(
            game=self.game, task=self.task, user=self.user, text='ok', status='Ok', points=1,
            time=_dt(5),
        )
        result = apply_timing_event(
            game=self.game, task_group=self.tg, user=self.user,
            action=ACTION_START, session_id=uuid4(), event_id='late-start', seq=1, now=_dt(10),
        )
        self.assertFalse(result['exists'])
        self.assertFalse(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.tg, user=self.user,
        ).exists())

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

    def test_team_actor_without_authoritative_row_has_no_guessed_duration(self):
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=90)),
        ]
        team = Team.objects.create(name='legacy-team')
        self.assertIsNone(
            canonical_elapsed_seconds(
                game=self.game,
                task_group=self.tg,
                user=self.user,
                team=team,
                attempts=attempts,
            ),
        )

    def test_team_shared_timer_is_one_row_across_member_sessions_and_pauses(self):
        team = Team.objects.create(name='timing-team', project_id='sections')
        page_context = daily_timing_page_context(
            None, self.game, self.link, team=team, play_mode='team',
        )
        self.assertTrue(page_context['daily_timing_enabled'])
        session_a = uuid4()
        session_b = uuid4()
        first = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_START, session_id=session_a, event_id='team-start-a', seq=1, now=_dt(),
        )
        # A second teammate's start takes over the same lease rather than making another timer.
        second = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_START, session_id=session_b, event_id='team-start-b', seq=1, now=_dt(5),
        )
        self.assertTrue(first['exists'])
        self.assertTrue(second['is_authoritative'])
        self.assertEqual(DailySolveTiming.objects.filter(team=team, game=self.game, task_group=self.tg).count(), 1)
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_HEARTBEAT, session_id=session_b, event_id='team-heartbeat-before-pause',
            seq=2, claimed_ms=5000, now=_dt(10),
        )

        paused = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_PAUSE, session_id=session_a, event_id='team-pause', seq=1,
            claimed_ms=5000, now=_dt(15),
        )
        self.assertEqual(paused['status'], DailySolveTiming.STATUS_MANUALLY_PAUSED)
        self.assertEqual(paused['committed_ms'], 5000)
        # A repeated pause is harmless; resume starts a new shared active interval.
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_PAUSE, session_id=session_b, event_id='team-pause-again', seq=2,
            now=_dt(12),
        )
        resumed = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_RESUME, session_id=session_b, event_id='team-resume', seq=3,
            now=_dt(20),
        )
        self.assertTrue(resumed['is_authoritative'])
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_HEARTBEAT, session_id=session_b, event_id='team-heartbeat-after-resume',
            seq=4, claimed_ms=10000, now=_dt(30),
        )
        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, team=team, now=_dt(30),
        )
        self.assertEqual(completed['frozen_ms'], 15000)
        self.assertEqual(
            canonical_elapsed_seconds(game=self.game, task_group=self.tg, team=team),
            15,
        )
        first_row = lookup_timing(game=self.game, task_group=self.tg, team=team)
        replay = ReplaySlot.objects.create(
            game=self.game, task_group=self.tg, team=team, actor_key='team:{}'.format(team.pk),
        )
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team, replay_slot=replay,
            action=ACTION_START, session_id=uuid4(), event_id='team-replay-start', seq=1, now=_dt(),
        )
        complete_daily_timing(game=self.game, task_group=self.tg, team=team, replay_slot=replay, now=_dt(90))
        self.assertEqual(lookup_timing(game=self.game, task_group=self.tg, team=team).pk, first_row.pk)
        self.assertEqual(DailySolveTiming.objects.filter(team=team, game=self.game, task_group=self.tg).count(), 2)

    def test_each_daily_section_type_uses_the_same_timing_lifecycle(self):
        team_ids = {'ladder', 'salad', 'replacements', 'walls', 'palindromes', 'week_task'}
        team = Team.objects.create(name='timing-all-sections', project_id='sections')
        for game_id in ('ladder', 'salad', 'alphabetty', 'replacements', 'walls', 'palindromes', 'week_task'):
            game = Game.objects.filter(pk=game_id).first()
            if game is None:
                game = Game.objects.create(
                    id=game_id, name=game_id, author='tests', project_id='sections', is_ready=True,
                )
            group = TaskGroup.objects.create(label='timing-{}'.format(game_id))
            GameTaskGroup.objects.create(game=game, task_group=group, number='991', name='Timing')
            actor_kwargs = {'team': team} if game_id in team_ids else {'user': self.user}
            sid = uuid4()
            started = apply_timing_event(
                game=game, task_group=group, action=ACTION_START, session_id=sid,
                event_id='{}-start'.format(game_id), seq=1, now=_dt(), **actor_kwargs,
            )
            self.assertTrue(started['exists'], game_id)
            apply_timing_event(
                game=game, task_group=group, action=ACTION_PAUSE, session_id=sid,
                event_id='{}-pause'.format(game_id), seq=2, claimed_ms=5000, now=_dt(5), **actor_kwargs,
            )
            apply_timing_event(
                game=game, task_group=group, action=ACTION_RESUME, session_id=sid,
                event_id='{}-resume'.format(game_id), seq=3, now=_dt(10), **actor_kwargs,
            )
            complete_daily_timing(game=game, task_group=group, now=_dt(20), **actor_kwargs)
            self.assertEqual(
                canonical_elapsed_seconds(game=game, task_group=group, **actor_kwargs), 15,
                game_id,
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

    def test_retries_mysql_deadlock_then_succeeds(self):
        calls = {'n': 0}
        orig = daily_timing_mod._apply_timing_event_once

        def flaky(**kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OperationalError(1213, 'Deadlock found when trying to get lock')
            return orig(**kwargs)

        sid = uuid4()
        with patch.object(daily_timing_mod, '_apply_timing_event_once', side_effect=flaky):
            with self.assertLogs('games.daily_timing', level='WARNING') as logs:
                snap = self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self.assertEqual(calls['n'], 2)
        self.assertTrue(snap['exists'])
        joined = '\n'.join(logs.output)
        self.assertIn('daily_timing deadlock retry attempt=1/3 action=start', joined)
        self.assertNotIn(self.user.username, joined)
        self.assertNotIn(self.user.email, joined)
        self.assertNotIn(str(sid), joined)

    def test_mysql_deadlock_retry_exhausts_without_other_operational_errors(self):
        calls = {'n': 0}

        def boom(**kwargs):
            calls['n'] += 1
            raise OperationalError(1213, 'Deadlock found when trying to get lock')

        sid = uuid4()
        with patch.object(daily_timing_mod, '_apply_timing_event_once', side_effect=boom):
            with self.assertLogs('games.daily_timing', level='WARNING') as logs:
                with self.assertRaises(OperationalError) as ctx:
                    self._apply(action=ACTION_HEARTBEAT, session=sid, seq=1, now=_dt())
        self.assertEqual(ctx.exception.args[0], 1213)
        self.assertEqual(calls['n'], TIMING_DEADLOCK_ATTEMPTS)
        joined = '\n'.join(logs.output)
        self.assertIn('daily_timing deadlock exhausted attempts=3 action=heartbeat', joined)
        self.assertNotIn(self.user.username, joined)
        self.assertNotIn(self.user.email, joined)

        calls['n'] = 0

        def timeout(**kwargs):
            calls['n'] += 1
            raise OperationalError(1205, 'Lock wait timeout exceeded')

        with patch.object(daily_timing_mod, '_apply_timing_event_once', side_effect=timeout):
            with self.assertRaises(OperationalError) as ctx:
                self._apply(action=ACTION_START, session=sid, seq=2, now=_dt())
        self.assertEqual(ctx.exception.args[0], 1205)
        self.assertEqual(calls['n'], 1)

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
        attach_anon_cookie(self.client, self.anon)

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
    def test_payload_and_header_cannot_spoof_anonymous_or_registered_actor(self, _pub):
        forged = 'attacker-chosen-anon'
        response = self.client.post(
            '/ladder/91002/timing/',
            data=json.dumps({
                'action': ACTION_START, 'session_id': str(uuid4()),
                'event_id': 'spoofed-actor', 'seq': 1,
                'anon_key': forged, 'user_id': 999999, 'team_id': 'not-my-team',
            }),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=forged,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(DailySolveTiming.objects.filter(
            anon_key=self.anon, game=self.game, task_group=self.tg,
        ).exists())
        self.assertFalse(DailySolveTiming.objects.filter(
            anon_key=forged, game=self.game, task_group=self.tg,
        ).exists())
        self.assertFalse(DailySolveTiming.objects.filter(
            user_id=999999, game=self.game, task_group=self.tg,
        ).exists())

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_replay_id_in_payload_cannot_select_replay_timing_namespace(self, _pub):
        slot = ReplaySlot.objects.create(
            game=self.game, task_group=self.tg, anon_key=self.anon,
            actor_key='a:{}'.format(self.anon),
        )
        response = self.client.post(
            '/ladder/91002/timing/',
            data=json.dumps({
                'action': ACTION_START, 'session_id': str(uuid4()),
                'event_id': 'spoofed-replay', 'seq': 1,
                'replay_slot_id': slot.pk, 'replay_run_id': 'not-current-session',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = DailySolveTiming.objects.get(game=self.game, task_group=self.tg)
        self.assertIsNone(row.replay_slot_id)

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_timing_mutation_keeps_gameplay_csrf_protection(self, _pub):
        strict_client = Client(enforce_csrf_checks=True)
        attach_anon_cookie(strict_client, 'csrf-timing-test')
        response = strict_client.post(
            '/ladder/91002/timing/',
            data=json.dumps({
                'action': ACTION_START, 'session_id': str(uuid4()),
                'event_id': 'csrf-missing', 'seq': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).exists())

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=False)
    def test_unpublished_release_cannot_start_timing(self, _pub):
        response = self._post({
            'action': ACTION_START, 'session_id': str(uuid4()),
            'event_id': 'private', 'seq': 1,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).exists())

    @patch('games.club_access.user_can_access_scheduled_number', return_value=False)
    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_inaccessible_release_cannot_start_timing(self, _pub, _access):
        response = self._post({
            'action': ACTION_START, 'session_id': str(uuid4()),
            'event_id': 'inaccessible', 'seq': 1,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).exists())

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

    @patch('games.club_access.user_can_access_scheduled_number', return_value=True)
    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    @patch('games.models.Game.has_access', return_value=True)
    def test_team_api_uses_server_resolved_shared_actor(self, _access, _pub, _club):
        team = Team.objects.create(name='timing-api-team', project_id='sections')
        user = User.objects.create_user('timing_team_user', 'team@example.com', 'secret')
        Profile.objects.create(user=user, first_name='T', last_name='M', team_on=team)
        self.client.force_login(user)
        session = self.client.session
        session['play_mode_sections'] = 'team'
        session.save()
        response = self.client.post(
            '/ladder/91002/timing/',
            data=json.dumps({
                'action': ACTION_START, 'session_id': str(uuid4()), 'event_id': 'team-start', 'seq': 1,
                'team_id': 'forged-other-team', 'user_id': 999999,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(DailySolveTiming.objects.filter(
            team=team, game=self.game, task_group=self.link.task_group,
        ).exists())
        self.assertFalse(DailySolveTiming.objects.filter(
            user=user, game=self.game, task_group=self.link.task_group,
        ).exists())

        teammate = User.objects.create_user('timing_team_user_b', 'team-b@example.com', 'secret')
        Profile.objects.create(user=teammate, first_name='T', last_name='N', team_on=team)
        other_client = Client()
        other_client.force_login(teammate)
        other_session = other_client.session
        other_session['play_mode_sections'] = 'team'
        other_session.save()
        teammate_session_id = str(uuid4())
        second_response = other_client.post(
            '/ladder/91002/timing/',
            data=json.dumps({'action': ACTION_START, 'session_id': teammate_session_id, 'event_id': 'team-start-b', 'seq': 1}),
            content_type='application/json',
        )
        self.assertEqual(second_response.status_code, 200, second_response.content)
        self.assertEqual(DailySolveTiming.objects.filter(
            team=team, game=self.game, task_group=self.link.task_group,
        ).count(), 1)
        finish = other_client.post(
            '/ladder/91002/timing/',
            data=json.dumps({'action': ACTION_COMPLETE, 'session_id': teammate_session_id, 'event_id': 'team-finish-b', 'seq': 2}),
            content_type='application/json',
        )
        self.assertEqual(finish.status_code, 200, finish.content)
        self.assertTrue(finish.json()['completed'])


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

    def _assert_missing_without_row(self, action):
        self.assertFalse(
            DailySolveTiming.objects.filter(
                anon_key=self.anon, game=self.game, task_group=self.tg,
            ).exists()
        )
        resp = self._post({
            'action': action,
            'session_id': str(uuid4()),
            'event_id': '{}-missing'.format(action),
            'seq': 1,
        })
        self.assertNotEqual(
            resp.status_code, 500,
            'exists=False must not raise; got {}'.format(resp.content[:300]),
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['error'], 'missing')
        self.assertFalse(data['exists'])
        self.assertFalse(
            DailySolveTiming.objects.filter(
                anon_key=self.anon, game=self.game, task_group=self.tg,
            ).exists()
        )

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_heartbeat_without_row_is_missing_not_500(self, _pub):
        self._assert_missing_without_row(ACTION_HEARTBEAT)

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_pause_without_row_is_missing_not_500(self, _pub):
        self._assert_missing_without_row(ACTION_PAUSE)

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_complete_without_row_is_missing_not_500(self, _pub):
        self._assert_missing_without_row(ACTION_COMPLETE)

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_resume_without_row_creates(self, _pub):
        resp = self._post({
            'action': ACTION_RESUME,
            'session_id': str(uuid4()),
            'event_id': 'resume-create',
            'seq': 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['exists'])
        self.assertEqual(data['status'], 'running')

    @patch('games.views.daily_timing_views.scheduled_number_is_public', return_value=True)
    def test_start_heartbeat_pause_resume_complete_flow(self, _pub):
        sid = str(uuid4())
        start = self._post({
            'action': ACTION_START,
            'session_id': sid,
            'event_id': 'flow-start',
            'seq': 1,
        })
        self.assertEqual(start.status_code, 200)
        self.assertTrue(start.json()['is_authoritative'])
        hb = self._post({
            'action': ACTION_HEARTBEAT,
            'session_id': sid,
            'event_id': 'flow-hb',
            'seq': 2,
            'claimed_ms': 1000,
        })
        self.assertEqual(hb.status_code, 200)
        self.assertEqual(hb.json()['status'], 'running')
        paused = self._post({
            'action': ACTION_PAUSE,
            'session_id': sid,
            'event_id': 'flow-pause',
            'seq': 3,
            'claimed_ms': 1000,
        })
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()['status'], 'manually_paused')
        resumed = self._post({
            'action': ACTION_RESUME,
            'session_id': sid,
            'event_id': 'flow-resume',
            'seq': 4,
        })
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()['status'], 'running')
        done = self._post({
            'action': ACTION_COMPLETE,
            'session_id': sid,
            'event_id': 'flow-complete',
            'seq': 5,
        })
        self.assertEqual(done.status_code, 200)
        data = done.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['completed'])
        self.assertEqual(data['status'], 'completed')

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
        self.assertTrue(is_daily_timing_game('week_task'))

    def test_missing_section_release_timing_url_404(self):
        resp = self.client.post(
            '/walls/1/timing/',
            data=json.dumps({'action': 'start', 'session_id': str(uuid4()), 'event_id': 'x', 'seq': 1}),
            content_type='application/json',
            HTTP_X_INTEROVES_ANON=self.anon,
        )
        self.assertEqual(resp.status_code, 404)


class DailyTimingAuditCommandTests(TestCase):
    def test_audit_reports_counts_without_mutation_or_anonymous_keys(self):
        from django.core.management import call_command
        from io import StringIO
        from games.models import Attempt

        game = Game.objects.get(pk='ladder')
        group = TaskGroup.objects.create(label='timing-audit')
        link = GameTaskGroup.objects.create(game=game, task_group=group, number='99991', name='Audit')
        task = Task.objects.create(task_group=group, number='1', points=1, checker_data='x', text='x')
        Attempt.manager.create(
            game=game, task=task, anon_key='secret-anonymous-key', text='x', status='Ok', points=1,
        )
        output = StringIO()
        call_command('audit_daily_solve_timing', game='ladder', stdout=output)
        report = output.getvalue()
        self.assertIn('actor=anon results=1 timed=0 missing=1', report)
        self.assertNotIn('secret-anonymous-key', report)
        self.assertFalse(DailySolveTiming.objects.filter(game=game, task_group=link.task_group).exists())

        DailySolveTiming.objects.create(
            game=game, task_group=group, anon_key='secret-anonymous-key',
            status=DailySolveTiming.STATUS_COMPLETED, accumulated_ms=1000, frozen_ms=1000,
        )
        output = StringIO()
        call_command('audit_daily_solve_timing', game='ladder', stdout=output)
        self.assertIn('actor=anon results=1 timed=1 missing=0', output.getvalue())
