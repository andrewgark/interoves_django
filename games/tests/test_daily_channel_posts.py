"""The daily channel poster registers ladder, salad and censorly, and not alphabetty."""

from django.test import SimpleTestCase

from games.daily.registry import get_daily_game
from games.ops.telegram.channels.daily_post import CHANNEL_TICK_ORDER, iter_channel_posts


class DailyChannelPostRegistryTests(SimpleTestCase):
    def test_tick_order_matches_registry_keys_and_skips_alphabetty(self):
        posts = iter_channel_posts()
        self.assertEqual(tuple(post.key for post in posts), CHANNEL_TICK_ORDER)
        self.assertEqual(
            tuple(get_daily_game(key).channel_post_key for key in CHANNEL_TICK_ORDER),
            CHANNEL_TICK_ORDER,
        )
        self.assertIsNone(get_daily_game('alphabetty').channel_post_key)
