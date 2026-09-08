from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from games.models import Game, GameTaskGroup, PlayerCompletedGame, PlayerStartedGame, TaskGroup
from games.telegram.admin_reports import (
    build_admin_report,
    collect_report_stats,
    format_comparison,
    process_admin_report_tick,
)
from games.telegram.models import TelegramAdminReport


MOSCOW = ZoneInfo('Europe/Moscow')


@override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_ADMIN_CHAT_ID='12345')
class TelegramAdminReportTests(TestCase):
    def setUp(self):
        self.game = Game.objects.get(id='ladder')
        self.group = TaskGroup.objects.create(label='ladder:1')
        GameTaskGroup.objects.create(game=self.game, task_group=self.group, number='1', name='Лесенка №1')
        self.user = User.objects.create_user('report-user')
        self.now = datetime(2026, 9, 8, 1, 25, tzinfo=MOSCOW)  # Tuesday: Monday report covers Sunday.

    def _start(self, when, *, user=None, anon_key=None, game_kind='raddle'):
        row = PlayerStartedGame.objects.create(
            game=self.game, task_group=self.group, game_kind=game_kind,
            game_instance_id='{}-{}'.format(game_kind, when.timestamp()),
            user=user, anon_key=anon_key,
        )
        PlayerStartedGame.objects.filter(pk=row.pk).update(started_at=when)

    def test_counts_registered_and_stable_anonymous_players(self):
        self._start(datetime(2026, 9, 7, 12, tzinfo=MOSCOW), user=self.user)
        self._start(datetime(2026, 9, 7, 13, tzinfo=MOSCOW), anon_key='stable-anon')
        stats = collect_report_stats(
            datetime(2026, 9, 7, tzinfo=MOSCOW), datetime(2026, 9, 8, tzinfo=MOSCOW),
        )
        self.assertEqual(stats['players'], 2)
        self.assertEqual(stats['registered_players'], 1)
        self.assertEqual(stats['anonymous_players'], 1)
        self.assertEqual(stats['ladder'], 2)

    def test_daily_report_has_both_comparison_dimensions_and_required_games(self):
        text, kind, _period = build_admin_report(now=self.now)
        self.assertEqual(kind, TelegramAdminReport.REPORT_DAILY)
        self.assertIn('к вчера', text)
        self.assertIn('к неделе', text)
        self.assertIn('«Лесенки»', text)
        self.assertIn('«Салатики»', text)
        self.assertIn('«Алфавитки»', text)
        self.assertLess(len(text), 4096)

    def test_monday_is_weekly_and_tick_sends_only_once(self):
        monday = datetime(2026, 9, 7, 1, 25, tzinfo=MOSCOW)
        text, kind, period = build_admin_report(now=monday)
        self.assertEqual(kind, TelegramAdminReport.REPORT_WEEKLY)
        self.assertIn('к пред. неделе', text)
        with patch('games.telegram.admin_reports.send_admin_message', return_value=True) as send:
            self.assertEqual(process_admin_report_tick(now=monday), {'sent': 1, 'skipped': 0})
            self.assertEqual(process_admin_report_tick(now=monday.replace(minute=26)), {'sent': 0, 'skipped': 1})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(TelegramAdminReport.objects.count(), 1)

    def test_comparison_format(self):
        self.assertEqual(format_comparison(0, 0), '⚪️ 0')
        self.assertIn('🟢📈 +2 (+100%)', format_comparison(4, 2))
        self.assertIn('🔴📉 −1 (−50%)', format_comparison(1, 2))
