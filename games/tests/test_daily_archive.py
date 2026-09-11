from datetime import date

from django.test import SimpleTestCase

from games.daily_archive import build_daily_archive_context


class DailyArchiveContextTests(SimpleTestCase):
    def items(self):
        return [
            {'date': date(2026, 3, 31), 'key': '1', 'number': 1, 'anchor': 'ladder-1', 'href': '/ladder/1/'},
            {'date': date(2026, 5, 2), 'key': '2', 'number': 2, 'anchor': 'ladder-2', 'href': '/ladder/2/'},
            {'date': date(2026, 5, 20), 'key': '3', 'number': 3, 'anchor': 'ladder-3', 'href': '/ladder/3/'},
        ]

    def test_default_month_prefers_current_month_then_latest(self):
        context = build_daily_archive_context(items=self.items(), today=date(2026, 5, 9), archive_url='/ladder/')
        self.assertEqual(context['daily_archive_month'], '2026-05')
        self.assertEqual(context['daily_archive_archive_label'], 'мая 2026')
        self.assertEqual(context['daily_archive_previous']['href'], '/ladder/?month=2026-03')
        self.assertEqual(context['daily_archive_previous']['month_label'], 'Март 2026')
        self.assertIsNone(context['daily_archive_next'])

        context = build_daily_archive_context(items=self.items(), today=date(2026, 7, 9), archive_url='/ladder/')
        self.assertEqual(context['daily_archive_month'], '2026-05')

    def test_invalid_month_falls_back_and_missing_months_are_skipped(self):
        context = build_daily_archive_context(items=self.items(), requested_month='not-a-month', today=date(2026, 5, 9), archive_url='/ladder/')
        self.assertEqual([m['key'] for m in context['daily_archive_months']], ['2026-03', '2026-05'])

    def test_states_and_anchors_are_game_neutral(self):
        context = build_daily_archive_context(items=self.items(), requested_month='2026-05', today=date(2026, 5, 2), completed_keys={'2'}, archive_url='/ladder/', game_label='Лесенка')
        cells = [cell for week in context['daily_archive_weeks'] for cell in week if cell['date'].month == 5]
        available = {cell['date'].day: cell for cell in cells}
        self.assertTrue(available[2]['is_today'])
        self.assertTrue(available[2]['is_completed'])
        self.assertEqual(available[2]['href'], '/ladder/?month=2026-05')
        self.assertIn('Лесенка №2', available[2]['aria_label'])
        self.assertFalse(available[3]['is_available'])
        self.assertIn('задания нет', next(cell for cell in cells if cell['date'].day == 3)['aria_label'])

    def test_archive_statuses_are_carried_to_calendar_days(self):
        context = build_daily_archive_context(
            items=self.items(), requested_month='2026-05', archive_url='/ladder/',
            completed_keys={'3'}, status_by_key={'2': 'partial', '3': 'solved'},
        )
        cells = [cell for week in context['daily_archive_weeks'] for cell in week]
        statuses = {
            cell['date'].day: cell['archive_status']
            for cell in cells if cell['archive_status']
        }
        self.assertEqual(statuses, {2: 'partial', 20: 'solved'})

    def test_month_urls_preserve_extra_query_parameters(self):
        context = build_daily_archive_context(
            items=self.items(), requested_month='2026-05', archive_url='/ladder/',
            archive_query='play_mode=team&tag=x',
        )
        self.assertEqual(
            context['daily_archive_next'], None,
        )
        self.assertEqual(
            context['daily_archive_previous']['href'],
            '/ladder/?month=2026-03&play_mode=team&tag=x',
        )
