import io
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from games.telegram.results_image import (
    _COMPACT_RESULTS_CSS,
    _SCREENSHOT_HIDE_CSS,
    _expanded_table_viewport,
    _fit_telegram_photo_png,
    _validate_table_capture,
)


class _FakeLocator:
    first = None

    def __init__(self, metrics):
        self.first = self
        self.metrics = metrics

    def evaluate(self, _script):
        return self.metrics


class _FakePage:
    def __init__(self, metrics):
        self._locator = _FakeLocator(metrics)

    def locator(self, _selector):
        return self._locator


class TournamentResultsScreenshotTests(SimpleTestCase):
    def test_screenshot_css_expands_scroll_container(self):
        compact = ' '.join(_SCREENSHOT_HIDE_CSS.split())
        self.assertIn(
            '.new-results-wrap { overflow: visible !important; '
            'width: max-content !important; max-width: none !important; '
            'max-height: none !important;',
            compact,
        )
        self.assertIn('max-width: none !important', compact)
        self.assertIn('text-overflow: clip !important', compact)

    def test_compact_css_hides_task_columns_and_rows_after_top_twenty(self):
        compact = ' '.join(_COMPACT_RESULTS_CSS.split())
        self.assertIn('thead tr:nth-child(2)', compact)
        self.assertIn('thead tr th:nth-child(n+5)', compact)
        self.assertIn('tbody td', compact)
        self.assertIn('tbody tr:nth-child(n+21)', compact)

    def test_viewport_expands_to_full_table_dimensions(self):
        viewport = _expanded_table_viewport(
            _FakePage({'width': 2460, 'height': 1180}),
            minimum_width=1400,
        )
        self.assertEqual(viewport, {'width': 2524, 'height': 1244})

    def test_viewport_dimensions_are_safely_capped(self):
        viewport = _expanded_table_viewport(
            _FakePage({'width': 20000, 'height': 12000}),
            minimum_width=1400,
        )
        self.assertEqual(viewport, {'width': 10000, 'height': 10000})

    @patch('games.telegram.results_image._TELEGRAM_MAX_DIMENSION_SUM', 1000)
    def test_telegram_photo_is_scaled_to_dimension_sum(self):
        source = io.BytesIO()
        Image.new('RGB', (800, 400), 'white').save(source, format='PNG')

        fitted = Image.open(io.BytesIO(_fit_telegram_photo_png(source.getvalue())))

        self.assertLessEqual(fitted.width + fitted.height, 1000)

    def test_telegram_photo_is_padded_to_supported_ratio(self):
        source = io.BytesIO()
        Image.new('RGB', (2500, 50), 'white').save(source, format='PNG')

        fitted = Image.open(io.BytesIO(_fit_telegram_photo_png(source.getvalue())))

        self.assertLessEqual(fitted.width / fitted.height, 19.5)

    def test_capture_validation_rejects_clipped_png(self):
        source = io.BytesIO()
        Image.new('RGB', (180, 100), 'white').save(source, format='PNG')

        with self.assertRaisesRegex(RuntimeError, 'screenshot is clipped'):
            _validate_table_capture(
                source.getvalue(),
                {'width': 100, 'height': 60},
                scale_factor=2,
            )

    def test_capture_validation_accepts_full_png(self):
        source = io.BytesIO()
        Image.new('RGB', (204, 124), 'white').save(source, format='PNG')

        _validate_table_capture(
            source.getvalue(),
            {'width': 100, 'height': 60},
            scale_factor=2,
        )
