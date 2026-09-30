from unittest import TestCase

from games.daily.share import (
    DAILY_SHARE_ADAPTERS,
    get_daily_share_adapter,
)


class DailyShareAdapterTests(TestCase):
    def test_registry_selects_share_adapter(self):
        self.assertIs(
            get_daily_share_adapter('salad'),
            DAILY_SHARE_ADAPTERS['salad'],
        )
        self.assertIs(
            get_daily_share_adapter('ladder'),
            DAILY_SHARE_ADAPTERS['ladder'],
        )

    def test_unknown_game_does_not_get_share_card_accidentally(self):
        self.assertIsNone(get_daily_share_adapter('future'))

    def test_legacy_fallback_is_explicit(self):
        self.assertIs(
            get_daily_share_adapter('future', fallback_key='ladder'),
            DAILY_SHARE_ADAPTERS['ladder'],
        )
