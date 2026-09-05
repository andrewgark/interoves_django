import io
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from games.telegram.results_image import (
    _CHROMIUM_MAX_CSS_SIDE,
    _COMPACT_RESULTS_CSS,
    _SCREENSHOT_HIDE_CSS,
    _capture_expanded_results_png,
    _expanded_table_viewport,
    _fit_telegram_photo_png,
    _prepare_results_table_for_screenshot,
    _validate_table_capture,
)

_SCROLLABLE_TABLE_HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
  body { margin: 0; }
  .new-results-fullbleed {
    width: 100vw;
    display: flex;
    justify-content: center;
  }
  .new-results-wrap {
    overflow: auto;
    max-height: min(72vh, 720px);
    display: inline-block;
    isolation: isolate;
    border: 1px solid #ccc;
  }
  .new-results-table { width: max-content; border-collapse: collapse; }
  .new-results-table th,
  .new-results-table td {
    min-width: 72px;
    height: 28px;
    border: 1px solid #bbb;
    white-space: nowrap;
    padding: 4px;
    box-sizing: border-box;
  }
</style>
</head>
<body>
  <div class="new-results-fullbleed">
    <div class="new-results-wrap">
      <table class="new-results-table">
        <thead>
          <tr>
            PLACEHOLDER_HEAD
          </tr>
        </thead>
        <tbody>
          PLACEHOLDER_BODY
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def _scrollable_table_html(*, cols: int = 18, rows: int = 30) -> str:
    head = ''.join('<th>C{}</th>'.format(i) for i in range(cols))
    body_rows = []
    for row in range(rows):
        cells = []
        for col in range(cols):
            style = ''
            if row == 0 and col == 0:
                style = ' style="background:#b41414"'
            elif row == rows - 1 and col == cols - 1:
                style = ' style="background:#14b428"'
            cells.append('<td{}>R{}C{}</td>'.format(style, row, col))
        body_rows.append('<tr>{}</tr>'.format(''.join(cells)))
    return (
        _SCROLLABLE_TABLE_HTML
        .replace('PLACEHOLDER_HEAD', head)
        .replace('PLACEHOLDER_BODY', '\n'.join(body_rows))
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
            'overflow-x: visible !important; overflow-y: visible !important; '
            'width: max-content !important; max-width: none !important; '
            'max-height: none !important; margin: 0 !important; '
            'isolation: auto !important;',
            compact,
        )
        self.assertIn('.new-ui--section { display: none !important; }', compact)
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
        self.assertEqual(viewport, {'width': 2508, 'height': 1228})

    def test_viewport_dimensions_are_safely_capped(self):
        viewport = _expanded_table_viewport(
            _FakePage({'width': 20000, 'height': 20000}),
            minimum_width=1400,
        )
        self.assertEqual(
            viewport,
            {'width': _CHROMIUM_MAX_CSS_SIDE, 'height': _CHROMIUM_MAX_CSS_SIDE},
        )

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

    def test_playwright_captures_full_scrollable_table(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SkipTest('Playwright Python package is not installed') from exc

        from games.telegram.ladder_image import _ensure_playwright_browsers_path

        _ensure_playwright_browsers_path()
        playwright = None
        browser = None
        try:
            playwright = sync_playwright().start()
            if not Path(playwright.chromium.executable_path).exists():
                raise SkipTest('Playwright Chromium is not installed')
            browser = playwright.chromium.launch(headless=True)
        except SkipTest:
            if playwright is not None:
                playwright.stop()
            raise
        except Exception as exc:
            if playwright is not None:
                playwright.stop()
            raise SkipTest('Playwright Chromium could not launch: {}'.format(exc)) from exc

        try:
            page = browser.new_page(
                viewport={'width': 400, 'height': 400},
                device_scale_factor=1,
            )
            page.set_content(_scrollable_table_html())
            _prepare_results_table_for_screenshot(page)
            png = _capture_expanded_results_png(
                page,
                viewport_width=400,
                scale_factor=1,
            )
        finally:
            browser.close()
            playwright.stop()

        image = Image.open(io.BytesIO(png)).convert('RGB')
        colors = set(image.getdata())
        self.assertGreater(image.width, 1000)
        self.assertGreater(image.height, 800)
        self.assertIn((180, 20, 20), colors)
        self.assertIn((20, 180, 40), colors)
