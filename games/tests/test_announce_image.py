from django.test import SimpleTestCase

from games.telegram.announce_image import announce_screenshot_url


class AnnounceScreenshotUrlTests(SimpleTestCase):
    def test_upcoming_game_uses_its_own_page(self):
        game = type('Game', (), {'id': 'des173', 'tags': {}, 'project_id': 'main'})()
        self.assertTrue(announce_screenshot_url(game).endswith('/games/des173/'))
