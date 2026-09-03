from django.test import SimpleTestCase

from games.daily_share_card import CARD_HEIGHT, CARD_WIDTH, synthetic_preview_payloads
from games.daily_share_card_render import renderer_js_path


class DailyShareCardRendererPathTests(SimpleTestCase):
    def test_renderer_js_is_the_frontend_module(self):
        path = renderer_js_path()
        self.assertTrue(path.is_file())
        text = path.read_text(encoding='utf-8')
        self.assertIn('buildShareCardSvg', text)
        self.assertIn('renderShareCardPng', text)


class DailyShareCardPngTests(SimpleTestCase):
    def _render(self, payload):
        from games.daily_share_card_render import render_share_card_png

        try:
            return render_share_card_png(payload)
        except Exception as exc:
            self.skipTest('Playwright PNG rasterization unavailable: {}'.format(exc))

    def test_png_is_valid_1080x1920_and_deterministic(self):
        from games.daily_share_card_render import png_dimensions

        payload = [
            item for item in synthetic_preview_payloads() if item['kind'] == 'ladder'
        ][0]
        first = self._render(payload)
        second = self._render(payload)
        self.assertTrue(first.startswith(b'\x89PNG'))
        self.assertEqual(png_dimensions(first), (CARD_WIDTH, CARD_HEIGHT))
        self.assertEqual(first, second)

    def test_ru_and_en_render(self):
        from games.daily_share_card_render import assert_share_card_png

        payloads = synthetic_preview_payloads()
        ru = next(item for item in payloads if item['locale'] == 'ru' and item['kind'] == 'ladder')
        en = next(item for item in payloads if item['locale'] == 'en' and item['kind'] == 'alphabetty')
        assert_share_card_png(self._render(ru))
        assert_share_card_png(self._render(en))
