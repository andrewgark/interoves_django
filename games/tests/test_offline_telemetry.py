"""games.gameplay.offline_telemetry: client reports, counters, alerts and the admin report section."""
import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase

from games.gameplay import offline_telemetry as telemetry

HOUR = datetime(2026, 10, 29, 12, 30, tzinfo=dt_timezone.utc)


def anon_request():
    request = RequestFactory().post('/offline_telemetry/', HTTP_REFERER='https://interoves.com/ladder/5/')
    request.user = type('Anon', (), {'is_authenticated': False})()
    return request


@patch('games.gameplay.offline_telemetry.gameplay_anon_key', return_value='anon-telemetry')
@patch('games.gameplay.offline_telemetry.log_auth_event')
class RecordClientEventsTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_known_kinds_are_counted_and_unknown_dropped(self, log, _anon):
        accepted = telemetry.record_client_events(anon_request(), [
            {'kind': 'queue_synced', 'detail': {'count': 7, 'task_id': 3}},
            {'kind': 'drop_tables', 'detail': {}},
            'not a dict',
        ])
        self.assertEqual(accepted, 1)
        now = datetime.now(dt_timezone.utc)
        self.assertEqual(telemetry.totals(now - timedelta(hours=1), now + timedelta(hours=1)), {'queue_synced': 7})
        self.assertEqual(log.call_args.kwargs['kind'], 'queue_synced')

    def test_detail_is_whitelisted_and_truncated(self, log, _anon):
        detail = telemetry.sanitize_detail({'error': 'x' * 500, 'word': 'ОТВЕТ', 'count': 2})
        self.assertEqual(set(detail), {'error', 'count'})
        self.assertEqual(len(detail['error']), telemetry.DETAIL_VALUE_MAX)

    def test_counts_are_capped_per_event_and_batch(self, log, _anon):
        events = [{'kind': 'queue_synced', 'detail': {'count': 10 ** 9}}] * 50
        self.assertEqual(telemetry.record_client_events(anon_request(), events), telemetry.MAX_EVENTS_PER_REPORT)
        now = datetime.now(dt_timezone.utc)
        self.assertEqual(
            telemetry.totals(now - timedelta(hours=1), now + timedelta(hours=1))['queue_synced'],
            telemetry.MAX_EVENTS_PER_REPORT * telemetry.MAX_COUNT_PER_EVENT,
        )

    @patch('games.ops.telegram.bot.alerts.publish_admin_alert')
    def test_bug_kinds_alert_once_per_player_window(self, publish, log, _anon):
        telemetry.record_client_events(anon_request(), [
            {'kind': 'assemble_failed', 'detail': {'task_id': 3}},
            {'kind': 'assemble_failed', 'detail': {'task_id': 3}},
            {'kind': 'pack_failed', 'detail': {'status': 'http'}},
        ])
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args.kwargs['alert'], 'offline_play')
        self.assertIn('Карточка не собралась', publish.call_args.kwargs['payload']['text'])


class CountersTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_totals_sum_whole_hours_in_the_period(self):
        telemetry.count('verdict_divergence', now=HOUR)
        telemetry.count('verdict_divergence', 2, now=HOUR + timedelta(hours=1))
        telemetry.count('verdict_divergence', 5, now=HOUR + timedelta(hours=5))
        start = HOUR.replace(minute=0)
        self.assertEqual(
            telemetry.totals(start, start + timedelta(hours=2)), {'verdict_divergence': 3},
        )

    def test_report_lines_list_only_what_happened(self):
        self.assertEqual(telemetry.report_lines(HOUR, HOUR + timedelta(hours=1)), [])
        telemetry.count('queue_synced', 4, now=HOUR)
        telemetry.count('offline_credit', now=HOUR)
        lines = telemetry.report_lines(HOUR.replace(minute=0), HOUR + timedelta(hours=1))
        self.assertEqual(lines[0], '<b>Офлайн-режим</b>')
        self.assertIn('Офлайн-ходов отправлено после восстановления связи: 4', lines)
        self.assertIn('Засчитано время офлайн-игры: 1', lines)


class TelemetryEndpointTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, body):
        return self.client.post('/offline_telemetry/', data=body, content_type='application/json')

    def test_accepts_a_batch(self):
        response = self._post(json.dumps({'events': [{'kind': 'sw_failed', 'detail': {'error': 'SecurityError'}}]}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'accepted': 1})

    def test_rejects_bad_json_and_huge_bodies(self):
        self.assertEqual(self._post('{nope').status_code, 400)
        self.assertEqual(self._post(json.dumps({'events': [], 'pad': 'x' * 20000})).status_code, 413)
        self.assertEqual(self.client.get('/offline_telemetry/').status_code, 405)


class AdminReportSectionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_daily_report_has_the_offline_section_when_something_happened(self):
        from games.ops.telegram.bot.reports import build_admin_report, report_periods

        now = datetime(2026, 10, 29, 21, 30, tzinfo=dt_timezone.utc)  # a Thursday night, daily report
        _kind, (start, _end), _ = report_periods(now)
        telemetry.count('queue_synced', 3, now=start + timedelta(hours=2))
        text, _kind, _period = build_admin_report(now=now)
        self.assertIn('<b>Офлайн-режим</b>', text)
        self.assertIn('Офлайн-ходов отправлено после восстановления связи: 3', text)
