from unittest import TestCase

from games.daily.page_context import build_daily_page_context, daily_statistics_url


class DailyPageContextTests(TestCase):
    def test_registered_game_uses_registry_labels(self):
        context = build_daily_page_context('salad', fallback_label='Fallback')

        self.assertEqual(context['daily_game_label'], 'Салатик')
        self.assertEqual(
            context['daily_pager_aria_label'],
            'Переход между салатиками',
        )

    def test_unknown_game_keeps_explicit_fallbacks(self):
        context = build_daily_page_context(
            'future',
            fallback_label='Будущая игра',
            fallback_pager_label='будущими играми',
        )

        self.assertEqual(context['daily_game_label'], 'Будущая игра')
        self.assertEqual(
            context['daily_pager_aria_label'],
            'Переход между будущими играми',
        )

    def test_statistics_url_respects_enabled_flag(self):
        self.assertEqual(
            daily_statistics_url('salad', 12, enabled=True),
            '/daily-statistics/salad/12/',
        )
        self.assertEqual(daily_statistics_url('salad', 12, enabled=False), '')
