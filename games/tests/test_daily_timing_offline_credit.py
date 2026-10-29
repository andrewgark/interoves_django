"""Offline play time: a start after offline play may claim the whole gap (docs/offline-play-plan.md)."""
import json
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from games.daily.timing import ACTION_COMPLETE, ACTION_START, apply_timing_event, lookup_timing
from games.daily.timing.session import (
    CLIENT_CREDIT_MAX_MS,
    OFFLINE_CREDIT_MAX_MS,
    complete_daily_timing_in_transaction,
    reduce_personal_timing_events,
)
from games.models import (
    CheckerType,
    DailyTimingEvent,
    Game,
    GameTaskGroup,
    OfflinePlayRule,
    Task,
    TaskGroup,
)
from games.ops.analytics.identity import attach_anon_cookie
from games.tests.test_daily_timing import _dt

MINUTE = 60_000


class OfflineCreditDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.game = Game.objects.get(id='ladder', project_id='sections')
        cls.tg = TaskGroup.objects.create(label='offline-credit-tg')
        GameTaskGroup.objects.create(game=cls.game, task_group=cls.tg, number='91101', name='T')
        cls.anon = 'anon-offline-credit'

    def _start(self, sid, seq, at, claimed=None, cap=None):
        return apply_timing_event(
            game=self.game, task_group=self.tg, anon_key=self.anon, action=ACTION_START,
            session_id=sid, event_id='start-{}'.format(seq), seq=seq, claimed_ms=claimed,
            now=at, credit_cap_ms=cap,
        )

    def _row(self):
        return lookup_timing(game=self.game, task_group=self.tg, anon_key=self.anon)

    def test_start_after_offline_play_credits_the_claim(self):
        sid = uuid4()
        self._start(sid, 1, _dt())
        # 40 minutes without the server, 35 of them visible and active.
        self._start(sid, 2, _dt(40 * 60), claimed=35 * MINUTE, cap=OFFLINE_CREDIT_MAX_MS)
        row = self._row()
        self.assertEqual(row.accumulated_ms, 35 * MINUTE)
        self.assertEqual(row.client_credited_ms, 35 * MINUTE)
        event = DailyTimingEvent.objects.get(event_id='start-2')
        self.assertEqual(event.credit_cap_ms, OFFLINE_CREDIT_MAX_MS)

    def test_ordinary_start_keeps_the_lost_heartbeat_cap(self):
        sid = uuid4()
        self._start(sid, 1, _dt())
        self._start(sid, 2, _dt(40 * 60), claimed=35 * MINUTE)
        self.assertEqual(self._row().accumulated_ms, CLIENT_CREDIT_MAX_MS)
        self.assertIsNone(DailyTimingEvent.objects.get(event_id='start-2').credit_cap_ms)

    def test_offline_claim_never_exceeds_the_server_gap(self):
        sid = uuid4()
        self._start(sid, 1, _dt())
        self._start(sid, 2, _dt(20 * 60), claimed=3 * 60 * MINUTE, cap=OFFLINE_CREDIT_MAX_MS)
        self.assertEqual(self._row().accumulated_ms, 20 * MINUTE)

    def test_completion_and_ledger_replay_keep_the_offline_credit(self):
        sid = uuid4()
        self._start(sid, 1, _dt())
        self._start(sid, 2, _dt(40 * 60), claimed=35 * MINUTE, cap=OFFLINE_CREDIT_MAX_MS)
        row = self._row()
        completed = complete_daily_timing_in_transaction(
            game=self.game, task_group=self.tg, anon_key=self.anon, now=_dt(40 * 60 + 30),
        )
        self.assertEqual(completed['frozen_ms'], 35 * MINUTE + 30_000)
        events = list(DailyTimingEvent.objects.filter(pk__in=[
            e.pk for e in DailyTimingEvent.objects.filter(game=self.game, task_group=self.tg)
        ]).order_by('occurred_at', 'pk'))
        reduced = reduce_personal_timing_events(
            [{**{f: getattr(e, f) for f in ('action', 'session_id', 'event_id', 'seq', 'claimed_ms',
                                              'credit_cap_ms')},
              'occurred_at': at}
             for e, at in zip(events, (_dt(), _dt(40 * 60)))]
            + [{'action': ACTION_COMPLETE, 'session_id': str(sid), 'event_id': 'complete', 'seq': 3,
                'occurred_at': _dt(40 * 60 + 30)}],
            now=_dt(40 * 60 + 30),
        )
        self.assertEqual(reduced['frozen_ms'], 35 * MINUTE + 30_000)
        self.assertEqual(reduced['client_credited_ms'], row.client_credited_ms)


@override_settings(OFFLINE_PLAY_ENABLED=True)
@patch('games.club.access.archive.user_can_access_scheduled_number', return_value=True)
@patch('games.models.Game.has_access', return_value=True)
@patch('games.site.views.daily.timing.scheduled_number_is_public', return_value=True)
class OfflineCreditApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.game = Game.objects.get(id='ladder', project_id='sections')
        self.tg = TaskGroup.objects.create(label='offline-credit-api')
        GameTaskGroup.objects.create(game=self.game, task_group=self.tg, number='91102', name='API')
        Task.objects.create(
            task_group=self.tg, number='1', task_type='raddle',
            checker=CheckerType.objects.get_or_create(pk='raddle')[0], points=1,
        )
        self.anon = 'anon-offline-credit-api'
        self.client = Client()
        attach_anon_cookie(self.client, self.anon)
        self.session = str(uuid4())

    def _post_at(self, payload, at):
        with patch('games.daily.timing.timezone.now', return_value=at):
            return self.client.post(
                '/ladder/91102/timing/', data=json.dumps(payload), content_type='application/json',
                HTTP_X_INTEROVES_ANON=self.anon,
            )

    def _offline_restart(self):
        first = self._post_at(
            {'action': ACTION_START, 'session_id': self.session, 'event_id': 'e1', 'seq': 1}, _dt(),
        )
        self.assertEqual(first.status_code, 200, first.content)
        return self._post_at({
            'action': ACTION_START, 'session_id': self.session, 'event_id': 'e2', 'seq': 2,
            'claimed_ms': 30 * MINUTE, 'offline': True,
        }, _dt(40 * 60))

    def _accumulated(self):
        return lookup_timing(game=self.game, task_group=self.tg, anon_key=self.anon).accumulated_ms

    def test_offline_start_is_credited_for_an_actor_who_may_time_offline_play(self, *_patches):
        OfflinePlayRule.objects.create(
            game=self.game, instant_verdict=True, offline_attempts=True, offline_timing=True,
        )
        self.assertEqual(self._offline_restart().status_code, 200)
        self.assertEqual(self._accumulated(), 30 * MINUTE)

    def test_offline_flag_without_the_capability_keeps_the_ordinary_cap(self, *_patches):
        OfflinePlayRule.objects.create(game=self.game, instant_verdict=True, offline_attempts=True)
        self.assertEqual(self._offline_restart().status_code, 200)
        self.assertEqual(self._accumulated(), CLIENT_CREDIT_MAX_MS)

    def test_offline_flag_on_a_heartbeat_is_ignored(self, *_patches):
        OfflinePlayRule.objects.create(
            game=self.game, instant_verdict=True, offline_attempts=True, offline_timing=True,
        )
        self._post_at({'action': ACTION_START, 'session_id': self.session, 'event_id': 'e1', 'seq': 1}, _dt())
        self._post_at({
            'action': 'heartbeat', 'session_id': self.session, 'event_id': 'e2', 'seq': 2,
            'claimed_ms': 30 * MINUTE, 'offline': True,
        }, _dt(10))
        self.assertIsNone(DailyTimingEvent.objects.get(event_id='e2').credit_cap_ms)
