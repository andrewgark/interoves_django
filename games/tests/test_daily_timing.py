from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, transaction
from django.test import Client, SimpleTestCase, TestCase
from django.urls import resolve
from django.utils import timezone

from games.ops.analytics.identity import attach_anon_cookie
from games import daily_timing as daily_timing_mod
from games.daily.page.section import is_daily_team_timing_game, is_daily_timing_game
from games.management.commands.repair_daily_attempt_timing import (
    TIMING_RECOVERY_GRACE_SECONDS,
    events_for_attempt,
)
from games.daily.timing import (
    ACTION_AUTO_PAUSE,
    ACTION_COMPLETE,
    ACTION_HEARTBEAT,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START,
    HEARTBEAT_MAX_CREDIT_MS,
    CLIENT_CREDIT_MAX_MS,
    TIMING_DEADLOCK_ATTEMPTS,
    _is_mysql_deadlock,
    apply_timing_event,
    canonical_elapsed_seconds,
    complete_daily_timing,
    lookup_timing,
    merge_timing_rows,
    migrate_timing_events,
    record_timing_event,
    record_gameplay_activity,
    reduce_personal_timing_events,
    recovered_timing_ms_from_events,
    reduce_team_timing_events,
)
from games.site.views.daily.timing import daily_timing_page_context
from games.models import (
    CheckerType,
    DailySolveTiming,
    DailySolveTimingSession,
    DailyTimingEvent,
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
from games.results.share import elapsed_seconds_from_attempts as share_elapsed
from games.puzzles.salad.rules import WORD_SALAD_GAME_ID


def _dt(seconds=0):
    return datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc) + timedelta(seconds=seconds)


class DailyTimingScopeTests(SimpleTestCase):
    def test_attempt_recovery_grace_includes_boundary_but_not_later_events(self):
        events = [
            SimpleNamespace(occurred_at=_dt(0)),
            SimpleNamespace(occurred_at=_dt(TIMING_RECOVERY_GRACE_SECONDS)),
            SimpleNamespace(occurred_at=_dt(TIMING_RECOVERY_GRACE_SECONDS + 0.001)),
        ]

        self.assertEqual(
            events_for_attempt(events, _dt()),
            events[:2],
        )
        self.assertEqual(
            events_for_attempt(events, _dt(), [event.occurred_at for event in events]),
            events[:2],
        )

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

    def test_timing_url_follows_the_play_page(self):
        ladder = daily_timing_page_context(
            None,
            SimpleNamespace(id='ladder'),
            SimpleNamespace(number='12', task_group=SimpleNamespace()),
        )
        self.assertEqual(ladder['daily_timing_url'], '/ladder/12/timing/')
        desyatka = daily_timing_page_context(
            None,
            SimpleNamespace(id='des173_test'),
            SimpleNamespace(number='1', task_group=SimpleNamespace()),
        )
        self.assertEqual(desyatka['daily_timing_url'], '/games/des173_test/1/timing/')
        match = resolve('/games/des173_test/1/timing/')
        self.assertEqual(match.func.__name__, 'daily_solve_timing')

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

    def test_personal_event_reducer_is_side_effect_free_and_idempotent(self):
        sid = str(uuid4())
        events = [
            {'action': ACTION_START, 'session_id': sid, 'event_id': 'start', 'seq': 1,
             'claimed_ms': 0, 'occurred_at': _dt(0)},
            {'action': ACTION_HEARTBEAT, 'session_id': sid, 'event_id': 'beat', 'seq': 2,
             'claimed_ms': 10000, 'occurred_at': _dt(10)},
            {'action': ACTION_HEARTBEAT, 'session_id': sid, 'event_id': 'beat', 'seq': 2,
             'claimed_ms': 10000, 'occurred_at': _dt(10)},
            {'action': ACTION_PAUSE, 'session_id': sid, 'event_id': 'pause', 'seq': 3,
             'claimed_ms': 2000, 'occurred_at': _dt(12)},
        ]
        reduced = reduce_personal_timing_events(events, now=_dt(12))
        self.assertEqual(reduced['status'], 'manually_paused')
        self.assertEqual(reduced['accumulated_ms'], 12000)
        self.assertEqual(reduced['event_count'], 4)
        self.assertEqual(reduced['applied_event_count'], 3)
        self.assertEqual(reduced['last_seq'], 3)

    def test_personal_replay_ignores_client_clock_skew_for_event_order(self):
        base = _dt(0)
        sid = str(uuid4())
        events = [
            {'action': ACTION_START, 'session_id': sid, 'event_id': 'start', 'seq': 1,
             'occurred_at': base, 'client_occurred_at': _dt(3600)},
            {'action': ACTION_PAUSE, 'session_id': sid, 'event_id': 'pause', 'seq': 2,
             'claimed_ms': 1000, 'occurred_at': _dt(1), 'client_occurred_at': _dt(-3600)},
        ]
        reduced = reduce_personal_timing_events(events, now=_dt(1))
        self.assertEqual(reduced['status'], 'manually_paused')
        self.assertEqual(reduced['accumulated_ms'], 1000)

    def test_reducer_replays_client_credit_and_activity_like_the_live_row(self):
        # Shape of a real case: heartbeats lost, the tab restarts every minute.
        events = [
            {'action': ACTION_START, 'session_id': 'a', 'event_id': 's1', 'seq': 38,
             'claimed_ms': 0, 'occurred_at': _dt(0)},
            {'action': ACTION_START, 'session_id': 'a', 'event_id': 's2', 'seq': 42,
             'claimed_ms': 58_000, 'occurred_at': _dt(61)},
            {'action': 'activity', 'session_id': 'a', 'event_id': 'activity:1', 'seq': 42,
             'claimed_ms': 0, 'occurred_at': _dt(90)},
            {'action': ACTION_COMPLETE, 'session_id': 'a', 'event_id': 'complete', 'seq': 43,
             'occurred_at': _dt(100)},
        ]
        reduced = reduce_personal_timing_events(events, now=_dt(100))
        self.assertEqual(reduced['frozen_ms'], 58_000 + 39_000)
        self.assertEqual(reduced['client_credited_ms'], 58_000)
        self.assertEqual(reduced['applied_event_count'], 4)

    def test_reducer_ignores_activity_for_a_session_that_never_started(self):
        events = [
            {'action': 'activity', 'session_id': 'a', 'event_id': 'activity:1', 'seq': 0,
             'occurred_at': _dt(0)},
            {'action': ACTION_COMPLETE, 'session_id': 'a', 'event_id': 'complete', 'seq': 1,
             'occurred_at': _dt(30)},
        ]
        self.assertEqual(reduce_personal_timing_events(events, now=_dt(30))['frozen_ms'], 0)

    def test_team_event_reducer_counts_union_of_sessions(self):
        events = [
            {'action': ACTION_START, 'session_id': 'a', 'event_id': 'a-start', 'seq': 1, 'occurred_at': _dt(0)},
            {'action': ACTION_START, 'session_id': 'b', 'event_id': 'b-start', 'seq': 1, 'occurred_at': _dt(5)},
            {'action': ACTION_PAUSE, 'session_id': 'a', 'event_id': 'a-pause', 'seq': 2, 'occurred_at': _dt(8)},
            {'action': ACTION_COMPLETE, 'session_id': 'b', 'event_id': 'b-complete', 'seq': 2, 'occurred_at': _dt(12)},
        ]
        reduced = reduce_team_timing_events(events, now=_dt(12))
        self.assertTrue(reduced['completed'])
        self.assertEqual(reduced['frozen_ms'], 12000)
        self.assertEqual(reduced['session_count'], 2)

    def test_heartbeat_just_after_lease_threshold_is_applied_before_expiry(self):
        events = [
            {'action': ACTION_START, 'session_id': 'a', 'event_id': 'a-start', 'seq': 1, 'occurred_at': _dt(0)},
            {'action': ACTION_HEARTBEAT, 'session_id': 'a', 'event_id': 'a-heartbeat', 'seq': 4, 'occurred_at': _dt(45.233)},
            {'action': ACTION_COMPLETE, 'session_id': 'b', 'event_id': 'b-complete', 'seq': 5, 'occurred_at': _dt(106.586)},
        ]

        reduced = reduce_personal_timing_events(events, now=_dt(106.586))

        self.assertTrue(reduced['completed'])
        self.assertEqual(reduced['frozen_ms'], 45_233)

    def test_recovered_completed_timing_rebuilds_missing_frozen_duration(self):
        sid = str(uuid4())
        events = [
            SimpleNamespace(
                action=ACTION_START, session_id=sid, event_id='start', seq=1,
                occurred_at=_dt(0), team_id=None,
            ),
            SimpleNamespace(
                action=ACTION_HEARTBEAT, session_id=sid, event_id='heartbeat', seq=2,
                occurred_at=_dt(10), team_id=None,
            ),
        ]

        self.assertEqual(
            recovered_timing_ms_from_events(events, completed_at=_dt(12)),
            12_000,
        )


class DailyTimingDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Project.objects.get_or_create(pk='sections', defaults={})
        HTMLPage.objects.get_or_create(name='Правила Десяточки', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила турнирного режима', defaults={'html': ''})
        HTMLPage.objects.get_or_create(name='Правила тренировочного режима', defaults={'html': ''})
        CheckerType.objects.get_or_create(pk='equals')
        CheckerType.objects.get_or_create(pk='equals_with_possible_spaces')
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

    def _apply(self, *, action, session, seq, event=None, claimed=None, now=None, user='user', team=None, create=True):
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
        if team is not None:
            kwargs['team'] = team
        elif user == 'user':
            kwargs['user'] = self.user
        else:
            kwargs['anon_key'] = self.anon
        return apply_timing_event(**kwargs)

    def test_repair_command_dry_run_and_apply_zero_frozen_row(self):
        completed_at = timezone.now()
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=0,
            frozen_ms=0,
            completed_at=completed_at,
        )
        event = DailyTimingEvent.objects.create(
            game=self.game,
            task_group=self.tg,
            user=self.user,
            actor_key='user:{}'.format(self.user.pk),
            session_id='repair-session',
            event_id='repair-start',
            action=ACTION_START,
            seq=1,
        )
        DailyTimingEvent.objects.filter(pk=event.pk).update(
            occurred_at=completed_at - timedelta(seconds=12),
        )

        dry_run = StringIO()
        call_command(
            'repair_daily_solve_timing',
            game=self.game.pk,
            task_group=self.tg.pk,
            user=self.user.pk,
            stdout=dry_run,
        )
        self.assertIn('would be repaired', dry_run.getvalue())
        row.refresh_from_db()
        self.assertEqual(row.frozen_ms, 0)

        call_command(
            'repair_daily_solve_timing',
            game=self.game.pk,
            task_group=self.tg.pk,
            user=self.user.pk,
            apply=True,
            stdout=StringIO(),
        )
        row.refresh_from_db()
        self.assertEqual(row.frozen_ms, 12_000)

    def test_repair_command_can_preview_and_apply_positive_underestimate(self):
        completed_at = timezone.now()
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=13_284,
            frozen_ms=13_284,
            completed_at=completed_at,
        )
        session_id = 'repair-positive-session'
        for action, event_id, seq, seconds_ago in (
            (ACTION_START, 'positive-start', 1, 30),
            (ACTION_HEARTBEAT, 'positive-heartbeat', 2, 15),
        ):
            event = DailyTimingEvent.objects.create(
                game=self.game,
                task_group=self.tg,
                user=self.user,
                actor_key='user:{}'.format(self.user.pk),
                session_id=session_id,
                event_id=event_id,
                action=action,
                seq=seq,
            )
            DailyTimingEvent.objects.filter(pk=event.pk).update(
                occurred_at=completed_at - timedelta(seconds=seconds_ago),
            )

        dry_run = StringIO()
        call_command(
            'repair_daily_solve_timing',
            game=self.game.pk,
            task_group=self.tg.pk,
            user=self.user.pk,
            include_underestimates=True,
            stdout=dry_run,
        )
        self.assertIn('frozen_ms=13284 recovered_ms=30000', dry_run.getvalue())
        row.refresh_from_db()
        self.assertEqual(row.frozen_ms, 13_284)

        call_command(
            'repair_daily_solve_timing',
            game=self.game.pk,
            task_group=self.tg.pk,
            user=self.user.pk,
            include_underestimates=True,
            apply=True,
            stdout=StringIO(),
        )
        row.refresh_from_db()
        self.assertEqual(row.frozen_ms, 30_000)
        self.assertEqual(row.accumulated_ms, 30_000)

    def test_completion_recovers_positive_underestimate_from_event_ledger(self):
        now = timezone.now().replace(microsecond=0)
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=13_284,
            frozen_ms=13_284,
            completed_at=now,
        )
        sid = 'underestimated-session'
        event_specs = [(ACTION_START, 1, 200)] + [
            (ACTION_HEARTBEAT, seq, seconds)
            for seq, seconds in enumerate(range(185, 4, -15), start=2)
        ]
        for action, seq, seconds_ago in event_specs:
            event = DailyTimingEvent.objects.create(
                game=self.game,
                task_group=self.tg,
                user=self.user,
                actor_key='user:{}'.format(self.user.pk),
                session_id=sid,
                event_id='ledger-{}'.format(seq),
                action=action,
                seq=seq,
                claimed_ms=0 if action == ACTION_START else 15_000,
            )
            DailyTimingEvent.objects.filter(pk=event.pk).update(
                occurred_at=now - timedelta(seconds=seconds_ago),
            )

        complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=now,
        )

        row.refresh_from_db()
        self.assertGreaterEqual(row.frozen_ms, 200_000)
        self.assertLess(row.frozen_ms, 201_000)
        self.assertEqual(row.accumulated_ms, row.frozen_ms)

    def test_completion_never_reduces_frozen_time_to_shorter_ledger_duration(self):
        now = timezone.now().replace(microsecond=0)
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=220_000,
            frozen_ms=220_000,
            completed_at=now,
        )
        event = DailyTimingEvent.objects.create(
            game=self.game,
            task_group=self.tg,
            user=self.user,
            actor_key='user:{}'.format(self.user.pk),
            session_id='short-ledger-session',
            event_id='short-ledger-start',
            action=ACTION_START,
            seq=1,
        )
        DailyTimingEvent.objects.filter(pk=event.pk).update(
            occurred_at=now - timedelta(seconds=10),
        )

        complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=now,
        )

        row.refresh_from_db()
        self.assertEqual(row.frozen_ms, 220_000)
        self.assertEqual(row.accumulated_ms, 220_000)

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

    def test_event_recording_skips_invalid_actor_shape(self):
        record_timing_event(
            game=self.game,
            task_group=self.tg,
            action=ACTION_START,
            session_id=str(uuid4()),
            event_id='invalid-actor',
            seq=1,
        )
        self.assertFalse(DailyTimingEvent.objects.filter(event_id='invalid-actor').exists())

    def test_ledger_failure_rolls_back_timing_snapshot_mutation(self):
        sid = uuid4()
        with patch(
            'games.daily.timing.record_timing_event',
            side_effect=RuntimeError('simulated ledger write failure'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'ledger write failure'):
                self._apply(action=ACTION_START, session=sid, seq=1, event='atomic-start')
        self.assertFalse(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.tg, user=self.user,
        ).exists())
        self.assertFalse(DailyTimingEvent.objects.filter(event_id='atomic-start').exists())

    def test_recovery_rebuilds_positive_duration_when_completion_snapshot_is_zero(self):
        sid = str(uuid4())
        events = [
            SimpleNamespace(
                action=ACTION_START, session_id=sid, event_id='start', seq=1,
                occurred_at=_dt(0), team_id=None,
            ),
            SimpleNamespace(
                action=ACTION_HEARTBEAT, session_id=sid, event_id='heartbeat', seq=2,
                occurred_at=_dt(10), team_id=None,
            ),
        ]
        self.assertEqual(
            recovered_timing_ms_from_events(events, completed_at=_dt(20)),
            20_000,
        )

    def test_event_identity_is_scoped_to_actor(self):
        session_id = str(uuid4())
        record_timing_event(
            game=self.game, task_group=self.tg, user=self.user,
            action=ACTION_START, session_id=session_id, event_id='same-event', seq=1,
        )
        record_timing_event(
            game=self.game, task_group=self.tg, anon_key=self.anon,
            action=ACTION_START, session_id=session_id, event_id='same-event', seq=1,
        )
        self.assertEqual(
            DailyTimingEvent.objects.filter(
                game=self.game, task_group=self.tg,
                session_id=session_id, event_id='same-event',
            ).count(),
            2,
        )

    def test_timing_events_follow_user_identity_merge(self):
        target = User.objects.create_user('timing_target', 'target@example.com', 'secret')
        Profile.objects.create(user=target, first_name='Target', last_name='User')
        event = DailyTimingEvent.objects.create(
            game=self.game,
            task_group=self.tg,
            user=self.user,
            actor_key='user:{}'.format(self.user.pk),
            session_id='merge-session',
            event_id='merge-event',
            action=ACTION_START,
            seq=1,
        )
        self.assertEqual(migrate_timing_events(target_user=target, source_user=self.user), 1)
        event.refresh_from_db()
        self.assertEqual(event.user_id, target.pk)
        self.assertIsNone(event.anon_key)
        self.assertEqual(event.actor_key, 'user:{}'.format(target.pk))

    def test_continuous_solve_accumulates_from_server_clock(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = self._apply(
            action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15),
        )
        self.assertEqual(snap['status'], 'running')
        self.assertEqual(snap['committed_ms'], 0)
        self.assertEqual(snap['accumulated_ms'], 15000)
        snap = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(20),
        )
        self.assertTrue(snap['completed'])
        self.assertEqual(snap['frozen_ms'], 20000)

    def test_auto_pause_arriving_before_initial_start_prevents_late_start(self):
        sid = uuid4()
        paused = self._apply(
            action=ACTION_AUTO_PAUSE,
            session=sid,
            seq=2,
            event='hide-before-start-response',
            now=_dt(3),
        )
        late_start = self._apply(
            action=ACTION_START,
            session=sid,
            seq=1,
            event='initial-start',
            now=_dt(),
        )

        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        session = row.sessions.get(session_id=sid)
        self.assertEqual(paused['status'], DailySolveTiming.STATUS_AUTO_PAUSED)
        self.assertEqual(late_start['status'], DailySolveTiming.STATUS_AUTO_PAUSED)
        self.assertEqual(session.last_seq, 2)
        self.assertEqual(session.close_reason, 'auto')
        self.assertIsNone(row.team_interval_started_at)

    def test_late_heartbeat_is_not_expired_before_updating_timing_row(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())

        heartbeat = self._apply(
            action=ACTION_HEARTBEAT, session=sid, seq=2,
            claimed=45233, now=_dt(45.233),
        )
        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(106.586),
        )

        self.assertEqual(heartbeat['status'], 'running')
        self.assertEqual(completed['frozen_ms'], 45_233)

    def test_start_after_own_lease_went_stale_reopens_the_interval(self):
        # Heartbeats lost on the network: the same tab restarts a minute later.
        # The restart used to leave the row without an open interval, so the
        # clock stayed at zero until completion recovered it from the ledger.
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        restarted = self._apply(action=ACTION_START, session=sid, seq=5, now=_dt(60))
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=6, claimed=15000, now=_dt(75))
        running = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=7, claimed=15000, now=_dt(90))

        self.assertTrue(restarted['is_authoritative'])
        self.assertEqual(restarted['active_sessions_count'], 1)
        self.assertEqual(running['accumulated_ms'], 30_000)
        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(100),
        )
        self.assertEqual(completed['frozen_ms'], 40_000)

    def _ledger(self):
        return list(DailyTimingEvent.objects.filter(
            game=self.game, task_group=self.tg, user=self.user,
        ).order_by('occurred_at', 'pk'))

    def test_restart_after_lost_heartbeats_credits_bounded_client_claim(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        # Visible for 60 s, but no heartbeat reached the server.
        restarted = self._apply(action=ACTION_START, session=sid, seq=5, claimed=58_000, now=_dt(60))

        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 58_000)
        self.assertEqual(row.client_credited_ms, 58_000)
        self.assertTrue(restarted['is_authoritative'])
        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(70),
        )
        self.assertEqual(completed['frozen_ms'], 68_000)

    def test_client_claim_never_exceeds_the_server_gap_or_the_idle_limit(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_START, session=sid, seq=2, claimed=999_999, now=_dt(60))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 60_000)

        self._apply(action=ACTION_START, session=sid, seq=3, claimed=10**9, now=_dt(60 + 3600))
        row.refresh_from_db()
        self.assertEqual(row.accumulated_ms, 60_000 + CLIENT_CREDIT_MAX_MS)

    def test_client_claim_is_ignored_for_a_fresh_lease_or_after_hidden_pause(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_START, session=sid, seq=2, claimed=30_000, now=_dt(30))
        self._apply(action=ACTION_AUTO_PAUSE, session=sid, seq=3, claimed=0, now=_dt(40))
        self._apply(action=ACTION_START, session=sid, seq=4, claimed=300_000, now=_dt(400))

        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 40_000)
        self.assertEqual(row.client_credited_ms, 0)

    def test_client_claim_is_ignored_when_another_tab_covered_the_gap(self):
        stale, other = uuid4(), uuid4()
        self._apply(action=ACTION_START, session=stale, seq=1, now=_dt())
        self._apply(action=ACTION_START, session=other, seq=1, now=_dt(10))
        self._apply(action=ACTION_HEARTBEAT, session=other, seq=2, now=_dt(25))
        self._apply(action=ACTION_HEARTBEAT, session=other, seq=3, now=_dt(40))
        self._apply(action=ACTION_HEARTBEAT, session=other, seq=4, now=_dt(55))
        self._apply(action=ACTION_START, session=stale, seq=5, claimed=60_000, now=_dt(60))

        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.client_credited_ms, 0)

    def test_gameplay_activity_keeps_the_lease_without_moving_client_seq(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=7, now=_dt())
        # A guess every 30 s keeps the lease alive although heartbeats are lost.
        record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=sid, now=_dt(30),
        )
        snap = record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=sid, now=_dt(60),
        )
        self.assertTrue(snap['is_authoritative'])
        self.assertEqual(snap['accumulated_ms'], 60_000)
        heartbeat = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=8, now=_dt(70))
        self.assertEqual(heartbeat['accumulated_ms'], 70_000)
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.sessions.get(session_id=sid).last_seq, 8)
        self.assertEqual(
            [event.action for event in self._ledger()].count('activity'), 2,
        )

        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(80),
        )
        self.assertEqual(completed['frozen_ms'], 80_000)

    def test_gameplay_activity_reopens_a_stale_lease_with_the_client_claim(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=sid,
            claimed_ms=50_000, now=_dt(50),
        )
        self.assertTrue(snap['is_authoritative'])
        self.assertEqual(snap['accumulated_ms'], 50_000)

    def test_gameplay_activity_never_opens_a_session_or_reverses_a_manual_pause(self):
        self.assertIsNone(record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=uuid4(), now=_dt(),
        ))
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_PAUSE, session=sid, seq=2, claimed=5000, now=_dt(5))
        record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=sid,
            claimed_ms=60_000, now=_dt(65),
        )
        unknown = record_gameplay_activity(
            game=self.game, task_group=self.tg, user=self.user, session_id=uuid4(), now=_dt(66),
        )
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.status, DailySolveTiming.STATUS_MANUALLY_PAUSED)
        self.assertEqual(row.accumulated_ms, 5000)
        self.assertEqual(row.sessions.count(), 1)
        self.assertFalse(unknown['is_authoritative'])

    def test_hidden_tab_does_not_keep_counting(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=8000, now=_dt(8))
        later = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=3, claimed=999999, now=_dt(3600))
        self.assertFalse(later['is_authoritative'])
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 8000)
        self.assertEqual(row.status, DailySolveTiming.STATUS_AUTO_PAUSED)

    def test_legacy_open_lease_snapshot_keeps_displaying_time_until_migration(self):
        sid = uuid4()
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            active_session_id=sid,
            interval_started_at=_dt(),
            last_heartbeat_at=_dt(),
        )
        snap = daily_timing_mod.snapshot(row, now=_dt(10), session_id=sid)
        self.assertTrue(snap['is_authoritative'])
        self.assertEqual(snap['accumulated_ms'], 10_000)

    def test_legacy_lease_is_expired_before_new_session_takes_over(self):
        legacy_sid = uuid4()
        row = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            active_session_id=legacy_sid,
            interval_started_at=_dt(),
            last_heartbeat_at=_dt(),
        )

        takeover = self._apply(action=ACTION_START, session=uuid4(), seq=1, now=_dt(60))

        row.refresh_from_db()
        self.assertEqual(takeover['committed_ms'], 0)
        self.assertEqual(takeover['active_sessions_count'], 1)
        self.assertEqual(
            row.sessions.filter(
                session_id=legacy_sid,
                status=DailySolveTimingSession.STATUS_PAUSED,
            ).count(),
            1,
        )

    def test_completion_migrates_legacy_lease_without_creating_extra_session(self):
        legacy_sid = uuid4()
        DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            active_session_id=legacy_sid,
            interval_started_at=_dt(),
            last_heartbeat_at=_dt(),
        )

        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(10),
        )

        self.assertTrue(completed['completed'])
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.sessions.count(), 1)
        self.assertEqual(row.sessions.get().session_id, legacy_sid)

    def test_return_to_tab_starts_new_interval(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=5000, now=_dt(5))
        self._apply(action=ACTION_START, session=sid, seq=3, now=_dt(3600))
        snap = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=4, claimed=10000, now=_dt(3610))
        self.assertEqual(snap['committed_ms'], 5000)
        self.assertEqual(snap['accumulated_ms'], 15000)

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
        self.assertEqual(first['accumulated_ms'], 15000)
        self.assertEqual(row.accumulated_ms, 0)

    def test_out_of_order_seq_is_ignored(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=4, claimed=15000, now=_dt(15))
        stale = self._apply(action=ACTION_HEARTBEAT, session=sid, seq=3, claimed=15000, now=_dt(30))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.accumulated_ms, 0)
        self.assertEqual(stale['committed_ms'], 0)
        self.assertEqual(stale['accumulated_ms'], 30000)

    def test_two_tabs_do_not_double_count(self):
        a = uuid4()
        b = uuid4()
        self._apply(action=ACTION_START, session=a, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=a, seq=2, claimed=15000, now=_dt(15))
        takeover = self._apply(action=ACTION_START, session=b, seq=3, now=_dt(20))
        self.assertTrue(takeover['is_authoritative'])
        ghost = self._apply(action=ACTION_HEARTBEAT, session=a, seq=4, claimed=15000, now=_dt(35))
        self.assertTrue(ghost['is_authoritative'])
        live = self._apply(action=ACTION_HEARTBEAT, session=b, seq=5, claimed=10000, now=_dt(30))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.active_sessions_count, 2)
        self.assertEqual(live['active_sessions_count'], 2)
        self.assertEqual(live['accumulated_ms'], 30000)

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
        self.assertEqual(row.active_sessions_count, 2)
        self.assertEqual(
            row.sessions.filter(session_id=a, status=DailySolveTimingSession.STATUS_RUNNING).count(), 1,
        )
        self.assertEqual(
            row.sessions.filter(session_id=b, status=DailySolveTimingSession.STATUS_RUNNING).count(), 1,
        )

    def test_foreign_auto_pause_does_not_kill_live_lease(self):
        a = uuid4()
        b = uuid4()
        self._apply(action=ACTION_START, session=a, seq=1, now=_dt())
        self._apply(action=ACTION_AUTO_PAUSE, session=b, seq=1, event='pause-b', claimed=0, now=_dt(3))
        row = lookup_timing(game=self.game, task_group=self.tg, user=self.user)
        self.assertEqual(row.status, DailySolveTiming.STATUS_RUNNING)
        self.assertIsNone(row.active_session_id)
        self.assertEqual(row.active_sessions_count, 1)
        self.assertEqual(
            row.sessions.filter(session_id=a, status=DailySolveTimingSession.STATUS_RUNNING).count(), 1,
        )
        live = self._apply(action=ACTION_HEARTBEAT, session=a, seq=2, claimed=10000, now=_dt(10))
        row.refresh_from_db()
        self.assertEqual(row.accumulated_ms, 0)
        self.assertEqual(live['accumulated_ms'], 10000)

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

    def test_existing_legacy_attempt_bootstraps_a_late_timer_start(self):
        from games.models import Attempt

        Attempt.manager.create(
            game=self.game, task=self.task, user=self.user, text='ok', status='Ok', points=1,
            time=_dt(5),
        )
        result = apply_timing_event(
            game=self.game, task_group=self.tg, user=self.user,
            action=ACTION_START, session_id=uuid4(), event_id='late-start', seq=1, now=_dt(10),
        )
        self.assertTrue(result['exists'])
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['accumulated_ms'], 0)
        self.assertTrue(DailySolveTiming.objects.filter(
            game=self.game, task_group=self.tg, user=self.user,
        ).exists())

    def test_ordinary_game_can_start_after_an_attempt_without_a_clock(self):
        from games.models import Attempt

        Project.objects.get_or_create(pk='main', defaults={})
        game = Game.objects.create(
            id='des_timing_probe', name='probe', author='t', project_id='main', is_ready=True,
        )
        tg = TaskGroup.objects.create(label='des-timing-tg')
        Task.objects.create(
            task_group=tg, number='1', checker=CheckerType.objects.get(pk='equals'),
            points=1, answer='ok',
        )
        Attempt.manager.create(
            game=game, task=tg.tasks.get(), user=self.user, text='x', status='Partial', points=0,
            time=_dt(5),
        )
        result = apply_timing_event(
            game=game, task_group=tg, user=self.user,
            action=ACTION_START, session_id=uuid4(), event_id='des-late-start', seq=1, now=_dt(10),
        )
        self.assertTrue(result['exists'])
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['accumulated_ms'], 0)

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
        self.assertEqual(snap['committed_ms'], 0)
        self.assertEqual(snap['accumulated_ms'], 10000)

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

    def test_non_daily_zero_sentinel_uses_the_attempt_clock(self):
        other = Game.objects.create(
            id='timing_sentinel_game',
            name='Sentinel',
            author='t',
            project_id='sections',
            is_ready=True,
        )
        DailySolveTiming.objects.create(
            user=self.user,
            game=other,
            task_group=self.tg,
            timing_version=1,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=0,
            frozen_ms=None,
            completed_at=_dt(),
        )
        t0 = _dt()
        attempts = [SimpleNamespace(time=t0), SimpleNamespace(time=t0)]
        self.assertEqual(
            canonical_elapsed_seconds(
                game=other, task_group=self.tg, user=self.user, attempts=attempts,
            ),
            0,
        )

    def test_daily_zero_sentinel_stays_unknown(self):
        DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            timing_version=1,
            status=DailySolveTiming.STATUS_COMPLETED,
            accumulated_ms=0,
            frozen_ms=None,
            completed_at=_dt(),
        )
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=40)),
        ]
        self.assertIsNone(
            canonical_elapsed_seconds(
                game=self.game, task_group=self.tg, user=self.user, attempts=attempts,
            ),
        )

    def test_team_actor_without_authoritative_row_has_no_guessed_duration(self):
        t0 = _dt()
        attempts = [
            SimpleNamespace(time=t0),
            SimpleNamespace(time=t0 + timedelta(seconds=90)),
        ]
        team = Team.objects.create(name='legacy-team', project_id='sections')
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
        # A second teammate starts an independent session on the same aggregate.
        second = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_START, session_id=session_b, event_id='team-start-b', seq=1, now=_dt(5),
        )
        self.assertTrue(first['exists'])
        self.assertTrue(second['is_authoritative'])
        self.assertEqual(second['active_sessions_count'], 2)
        self.assertEqual(DailySolveTiming.objects.filter(team=team, game=self.game, task_group=self.tg).count(), 1)
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_HEARTBEAT, session_id=session_b, event_id='team-heartbeat-before-pause',
            seq=2, claimed_ms=5000, now=_dt(10),
        )

        paused = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_PAUSE, session_id=session_a, event_id='team-pause', seq=2,
            claimed_ms=5000, now=_dt(15),
        )
        self.assertEqual(paused['status'], DailySolveTiming.STATUS_RUNNING)
        self.assertEqual(paused['committed_ms'], 0)
        self.assertEqual(paused['active_sessions_count'], 1)
        # Pausing A is local; pausing B later ends the shared active interval.
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_PAUSE, session_id=session_b, event_id='team-pause-again', seq=3,
            now=_dt(15),
        )
        resumed = apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_RESUME, session_id=session_b, event_id='team-resume', seq=4,
            now=_dt(20),
        )
        self.assertTrue(resumed['is_authoritative'])
        apply_timing_event(
            game=self.game, task_group=self.tg, team=team,
            action=ACTION_HEARTBEAT, session_id=session_b, event_id='team-heartbeat-after-resume',
            seq=5, claimed_ms=10000, now=_dt(30),
        )
        completed = complete_daily_timing(
            game=self.game, task_group=self.tg, team=team, now=_dt(30),
        )
        self.assertEqual(completed['frozen_ms'], 25000)
        self.assertEqual(
            canonical_elapsed_seconds(game=self.game, task_group=self.tg, team=team),
            25,
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

    def test_team_member_pause_does_not_pause_other_session(self):
        team = Team.objects.create(name='timing-session-scoped-pause', project_id='sections')
        session_a, session_b = uuid4(), uuid4()
        self._apply(action=ACTION_START, session=session_a, seq=1, now=_dt(), team=team)
        self._apply(action=ACTION_START, session=session_b, seq=1, now=_dt(2), team=team)
        paused = self._apply(
            action=ACTION_PAUSE, session=session_a, seq=2, now=_dt(5), team=team,
        )
        self.assertEqual(paused['status'], DailySolveTiming.STATUS_RUNNING)
        self.assertEqual(paused['active_sessions_count'], 1)
        self.assertFalse(paused['session_active'])
        other = self._apply(
            action=ACTION_HEARTBEAT, session=session_b, seq=2, now=_dt(10), team=team,
        )
        self.assertTrue(other['session_active'])
        self.assertEqual(other['active_sessions_count'], 1)
        self.assertEqual(other['committed_ms'], 0)
        row = lookup_timing(game=self.game, task_group=self.tg, team=team)
        self.assertEqual(
            DailySolveTimingSession.objects.filter(
                timing=row, status=DailySolveTimingSession.STATUS_PAUSED,
            ).count(),
            1,
        )

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
        self.assertEqual(snap['committed_ms'], 60_000)

    def test_auto_pause_credits_claimed_open_interval(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        snap = self._apply(
            action=ACTION_AUTO_PAUSE, session=sid, seq=2, claimed=90000, now=_dt(90),
        )
        self.assertEqual(snap['status'], 'auto_paused')
        self.assertEqual(snap['committed_ms'], 45_000)

    def test_complete_credits_open_interval_beyond_heartbeat_cap(self):
        sid = uuid4()
        self._apply(action=ACTION_START, session=sid, seq=1, now=_dt())
        self._apply(action=ACTION_HEARTBEAT, session=sid, seq=2, claimed=15000, now=_dt(15))
        snap = complete_daily_timing(
            game=self.game, task_group=self.tg, user=self.user, now=_dt(15 + 90),
        )
        self.assertEqual(snap['frozen_ms'], 15000)

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
        self.assertIn('mysql lock retry attempt=1/3 label=daily_timing action=start errno=1213', joined)
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
        self.assertIn('daily_timing lock retries exhausted attempts=3 action=heartbeat errno=1213', joined)
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
        self.assertEqual(calls['n'], TIMING_DEADLOCK_ATTEMPTS)

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

    def test_merge_moves_running_sessions_to_the_surviving_row(self):
        target = DailySolveTiming.objects.create(
            user=self.user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            active_sessions_count=1,
            team_interval_started_at=_dt(3),
        )
        source_user = User.objects.create_user('timing_src_running', 'sr@example.com', 'secret')
        source = DailySolveTiming.objects.create(
            user=source_user,
            game=self.game,
            task_group=self.tg,
            status=DailySolveTiming.STATUS_RUNNING,
            accumulated_ms=8000,
            active_sessions_count=1,
            team_interval_started_at=_dt(1),
        )
        target_session = DailySolveTimingSession.objects.create(
            timing=target,
            session_id=uuid4(),
            status=DailySolveTimingSession.STATUS_RUNNING,
            started_at=_dt(3),
            last_heartbeat_at=_dt(4),
        )
        source_session = DailySolveTimingSession.objects.create(
            timing=source,
            session_id=uuid4(),
            status=DailySolveTimingSession.STATUS_RUNNING,
            started_at=_dt(1),
            last_heartbeat_at=_dt(2),
        )

        merge_timing_rows(target, source)

        target.refresh_from_db()
        self.assertFalse(DailySolveTiming.objects.filter(pk=source.pk).exists())
        self.assertEqual(target.status, DailySolveTiming.STATUS_RUNNING)
        self.assertEqual(target.active_sessions_count, 2)
        self.assertEqual(target.team_interval_started_at, _dt(1))
        self.assertCountEqual(
            target.sessions.values_list('session_id', flat=True),
            [target_session.session_id, source_session.session_id],
        )


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

    def _post_at(self, client, payload, at):
        import json
        with patch('games.daily.timing.timezone.now', return_value=at):
            return client.post(
                '/ladder/91002/timing/',
                data=json.dumps(payload),
                content_type='application/json',
            )

    def _team_client(self, user, team):
        Profile.objects.create(user=user, first_name=user.username, last_name='T', team_on=team)
        client = Client()
        client.force_login(user)
        session = client.session
        session['play_mode_sections'] = 'team'
        session.save()
        return client

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_anonymous_start_and_pause(self, _pub):
        sid = uuid4()
        event = {
            'action': ACTION_START,
            'session_id': str(sid),
            'event_id': 'e1',
            'seq': 1,
            'event_protocol': 1,
            'event_capability': 'stable-event-v1',
            'client_occurred_at': timezone.now().isoformat(),
        }
        resp = self._post(event)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'running')
        self.assertTrue(data['is_authoritative'])
        duplicate = self._post(event)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()['ok'])
        self.assertEqual(
            DailyTimingEvent.objects.filter(
                game=self.game,
                task_group=self.tg,
                session_id=str(sid),
                event_id='e1',
            ).count(),
            1,
        )
        self.assertIsNotNone(DailyTimingEvent.objects.get(event_id='e1').client_occurred_at)

        mismatch = dict(event, action='pause', claimed_ms=0)
        conflict = self._post(mismatch)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['error'], 'event_id_conflict')
        self.assertEqual(DailyTimingEvent.objects.filter(event_id='e1').count(), 1)
        self.assertEqual(
            DailySolveTiming.objects.get(game=self.game, task_group=self.tg, anon_key=self.anon).status,
            DailySolveTiming.STATUS_RUNNING,
        )

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_timing_get_advertises_stable_event_capability(self, _pub):
        response = self.client.get('/ladder/91002/timing/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['timing_event_capability'], 'stable-event-v1')

    @patch(
        'games.site.views.daily.timing.get_object_or_404',
        side_effect=OperationalError(1040, 'Too many connections'),
    )
    def test_mysql_1040_during_timing_get_target_lookup_returns_retryable_503(self, _lookup):
        response = Client(raise_request_exception=False).get('/ladder/91002/timing/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '2')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(response.json()['ok'])

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    @patch(
        'games.site.views.daily.timing.apply_timing_event',
        side_effect=OperationalError(1205, 'Lock wait timeout exceeded'),
    )
    def test_lock_contention_returns_retryable_unavailable_instead_of_500(self, _apply, _pub):
        response = self._post({
            'action': ACTION_START,
            'session_id': str(uuid4()),
            'event_id': 'busy-start',
            'seq': 1,
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'temporarily_unavailable')
        self.assertEqual(response['Retry-After'], '2')
        self.assertFalse(response.json()['ok'])

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=False)
    def test_unpublished_release_cannot_start_timing(self, _pub):
        response = self._post({
            'action': ACTION_START, 'session_id': str(uuid4()),
            'event_id': 'private', 'seq': 1,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).exists())

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=False)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_inaccessible_release_cannot_start_timing(self, _pub, _access):
        response = self._post({
            'action': ACTION_START, 'session_id': str(uuid4()),
            'event_id': 'inaccessible', 'seq': 1,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DailySolveTiming.objects.filter(game=self.game, task_group=self.tg).exists())

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    def test_ordinary_game_timer_uses_the_page_team_actor(self, _club):
        from games.site.session.gameplay_context import issue_gameplay_context

        Project.objects.get_or_create(pk='main')
        game = Game.objects.create(
            id='des174_test', name='Timing probe', author='t', project_id='main',
            is_ready=True, rules=None, tournament_rules=None, general_rules=None,
        )
        GameTaskGroup.objects.create(game=game, task_group=self.tg, number='1')
        team = Team.objects.create(name='ordinary-timing-team', project_id='main')
        user = User.objects.create_user('ordinary_timing_player')
        client = self._team_client(user, team)
        session = client.session
        session['play_mode_main'] = 'team'
        session.save()
        token = issue_gameplay_context(game=game, task_group=self.tg, team=team)
        url = '/games/des174_test/1/timing/'
        sid = str(uuid4())

        # Like a tournament page: team play is allowed, personal play is not.
        def team_access(access, **kwargs):
            return access == 'play' and kwargs.get('team') == team

        with patch('games.models.Game.has_access', side_effect=team_access):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, response.content)
            for seq, action in enumerate((ACTION_START, ACTION_HEARTBEAT, ACTION_PAUSE), 1):
                response = client.post(
                    url,
                    data=json.dumps({
                        'action': action, 'session_id': sid,
                        'event_id': 'ordinary-team-{}'.format(seq), 'seq': seq,
                        'user_id': user.pk, 'team_id': 'forged-team',
                    }),
                    content_type='application/json',
                    HTTP_X_INTEROVES_GAMEPLAY_CONTEXT=token,
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(response.json()['ok'])

        row = DailySolveTiming.objects.get(game=game, task_group=self.tg)
        self.assertEqual(row.team, team)
        self.assertIsNone(row.user_id)
        self.assertEqual(row.status, DailySolveTiming.STATUS_MANUALLY_PAUSED)
        with patch('games.models.Game.has_access', return_value=False):
            denied = client.get(url)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error'], 'team_access_required')

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    @patch('games.models.Game.has_access', return_value=True)
    def test_team_http_sessions_pause_locally_and_count_union_once(self, _access, _pub, _club):
        team = Team.objects.create(name='timing-http-union-team', project_id='sections')
        first = self._team_client(
            User.objects.create_user('timing_http_team_a', 'http-a@example.com', 'secret'), team,
        )
        second = self._team_client(
            User.objects.create_user('timing_http_team_b', 'http-b@example.com', 'secret'), team,
        )
        session_a, session_b = str(uuid4()), str(uuid4())
        t0 = datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc)

        started_a = self._post_at(first, {
            'action': ACTION_START, 'session_id': session_a, 'event_id': 'http-a-start', 'seq': 1,
        }, t0)
        self.assertEqual(started_a.status_code, 200)
        started_b = self._post_at(second, {
            'action': ACTION_START, 'session_id': session_b, 'event_id': 'http-b-start', 'seq': 1,
        }, t0 + timedelta(seconds=5))
        self.assertEqual(started_b.status_code, 200)
        self.assertEqual(started_b.json()['active_sessions_count'], 2)

        paused_a = self._post_at(first, {
            'action': ACTION_PAUSE, 'session_id': session_a, 'event_id': 'http-a-pause', 'seq': 2,
        }, t0 + timedelta(seconds=15))
        self.assertEqual(paused_a.status_code, 200)
        self.assertEqual(paused_a.json()['status'], DailySolveTiming.STATUS_RUNNING)
        self.assertFalse(paused_a.json()['session_active'])
        self.assertEqual(paused_a.json()['active_sessions_count'], 1)

        self._post_at(second, {
            'action': ACTION_HEARTBEAT, 'session_id': session_b,
            'event_id': 'http-b-heartbeat', 'seq': 2,
        }, t0 + timedelta(seconds=20))
        completed = self._post_at(second, {
            'action': ACTION_COMPLETE, 'session_id': session_b,
            'event_id': 'http-b-complete', 'seq': 3,
        }, t0 + timedelta(seconds=30))
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()['frozen_ms'], 30_000)
        self.assertEqual(completed.json()['accumulated_ms'], 30_000)

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    @patch('games.models.Game.has_access', return_value=True)
    def test_personal_http_sessions_run_in_parallel_and_count_union_once(self, _access, _pub, _game_access):
        user = User.objects.create_user('timing_http_personal', 'http-personal@example.com', 'secret')
        Profile.objects.create(user=user, first_name='P', last_name='T')
        client = Client()
        client.force_login(user)
        session_a, session_b = str(uuid4()), str(uuid4())
        t0 = datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc)

        self.assertEqual(self._post_at(client, {
            'action': ACTION_START, 'session_id': session_a, 'event_id': 'personal-a-start', 'seq': 1,
        }, t0).status_code, 200)
        self.assertEqual(self._post_at(client, {
            'action': ACTION_HEARTBEAT, 'session_id': session_a,
            'event_id': 'personal-a-heartbeat', 'seq': 2,
        }, t0 + timedelta(seconds=5)).status_code, 200)
        second_start = self._post_at(client, {
            'action': ACTION_START, 'session_id': session_b, 'event_id': 'personal-b-start', 'seq': 1,
        }, t0 + timedelta(seconds=10))
        self.assertEqual(second_start.status_code, 200)
        self.assertTrue(second_start.json()['is_authoritative'])
        self.assertEqual(second_start.json()['active_sessions_count'], 2)
        self.assertEqual(second_start.json()['accumulated_ms'], 10_000)

        completed = self._post_at(client, {
            'action': ACTION_COMPLETE, 'session_id': session_b,
            'event_id': 'personal-b-complete', 'seq': 2,
        }, t0 + timedelta(seconds=20))
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()['frozen_ms'], 20_000)

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    @patch('games.models.Game.has_access', return_value=True)
    def test_http_clients_cannot_send_server_side_activity(self, _access, _pub, _game_access):
        user = User.objects.create_user('timing_http_activity', 'http-activity@example.com', 'secret')
        Profile.objects.create(user=user, first_name='A', last_name='T')
        client = Client()
        client.force_login(user)
        response = self._post_at(client, {
            'action': 'activity', 'session_id': str(uuid4()), 'event_id': 'forged', 'seq': 1,
        }, datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'bad_action')

    @patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    @patch('games.models.Game.has_access', return_value=True)
    def test_anonymous_http_sessions_run_in_parallel_and_count_union_once(self, _access, _pub, _game_access):
        session_a, session_b = str(uuid4()), str(uuid4())
        t0 = datetime(2026, 9, 3, 10, 0, 0, tzinfo=dt_timezone.utc)

        started_a = self._post_at(self.client, {
            'action': ACTION_START, 'session_id': session_a, 'event_id': 'anon-a-start', 'seq': 1,
        }, t0)
        self.assertEqual(started_a.status_code, 200)
        started_b = self._post_at(self.client, {
            'action': ACTION_START, 'session_id': session_b, 'event_id': 'anon-b-start', 'seq': 1,
        }, t0 + timedelta(seconds=5))
        self.assertEqual(started_b.status_code, 200)
        self.assertTrue(started_b.json()['is_authoritative'])
        self.assertEqual(started_b.json()['active_sessions_count'], 2)
        self.assertEqual(started_b.json()['accumulated_ms'], 5_000)

        paused_a = self._post_at(self.client, {
            'action': ACTION_PAUSE, 'session_id': session_a, 'event_id': 'anon-a-pause', 'seq': 2,
        }, t0 + timedelta(seconds=10))
        self.assertEqual(paused_a.status_code, 200)
        self.assertFalse(paused_a.json()['session_active'])
        self.assertEqual(paused_a.json()['active_sessions_count'], 1)

        completed = self._post_at(self.client, {
            'action': ACTION_COMPLETE, 'session_id': session_b,
            'event_id': 'anon-b-complete', 'seq': 2,
        }, t0 + timedelta(seconds=15))
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()['frozen_ms'], 15_000)


    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_malformed_numeric_timing_payload_is_400_not_500(self, _pub):
        for key in ('seq', 'claimed_ms'):
            resp = self._post({
                'action': ACTION_START,
                'session_id': str(uuid4()),
                'event_id': 'bad-{}'.format(key),
                key: 'not-a-number',
            })
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()['error'], 'bad_{}'.format(key))

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_malformed_timing_request_is_400_not_empty_start(self, _pub):
        invalid_json = self.client.post(
            '/ladder/91002/timing/', data='{', content_type='application/json',
            HTTP_X_INTEROVES_ANON=self.anon,
        )
        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(invalid_json.json()['error'], 'invalid_json')

        missing_ids = self._post({'action': ACTION_START, 'seq': 1})
        self.assertEqual(missing_ids.status_code, 400)
        self.assertEqual(missing_ids.json()['error'], 'bad_session_id')

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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_heartbeat_without_row_is_missing_not_500(self, _pub):
        self._assert_missing_without_row(ACTION_HEARTBEAT)

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_pause_without_row_is_missing_not_500(self, _pub):
        session_id = str(uuid4())
        paused = self._post({
            'action': ACTION_PAUSE,
            'session_id': session_id,
            'event_id': 'pause-before-start',
            'seq': 2,
        })
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()['ok'])
        self.assertEqual(paused.json()['status'], DailySolveTiming.STATUS_MANUALLY_PAUSED)

        late_start = self._post({
            'action': ACTION_START,
            'session_id': session_id,
            'event_id': 'late-start-after-pause',
            'seq': 1,
        })
        self.assertEqual(late_start.status_code, 200)
        self.assertEqual(late_start.json()['status'], DailySolveTiming.STATUS_MANUALLY_PAUSED)
        session = DailySolveTiming.objects.get(
            anon_key=self.anon, game=self.game, task_group=self.tg,
        ).sessions.get(session_id=session_id)
        self.assertEqual(session.last_seq, 2)

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
    def test_complete_without_row_is_missing_not_500(self, _pub):
        self._assert_missing_without_row(ACTION_COMPLETE)

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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

    @patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
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
