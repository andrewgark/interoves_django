from django.test import SimpleTestCase

from games.twitter.api import html_caption_to_plain


class TwitterCaptionTests(SimpleTestCase):
    def test_strips_telegram_html(self):
        plain = html_caption_to_plain(
            '🪜 <b>Лесенка №1</b>\n\nОт <b>ПАРИЖ</b> до <b>ДАКАР</b>\n\nhttps://interoves.com/games/ladder/1/'
        )
        self.assertEqual(
            plain,
            '🪜 Лесенка №1\n\nОт ПАРИЖ до ДАКАР\n\nhttps://interoves.com/games/ladder/1/',
        )
        self.assertNotIn('<', plain)
