"""Screenshot of tournament results table for Telegram /des_results."""

from __future__ import annotations

import io
import logging

from django.conf import settings

from games.telegram.game_urls import game_tournament_results_url
from games.telegram.ladder_image import _add_white_frame

logger = logging.getLogger('application')

_DEVICE_SCALE_FACTOR = 2
_CHROMIUM_MAX_CSS_SIDE = 16384
_TELEGRAM_MAX_DIMENSION_SUM = 9800  # Bot API hard limit is 10,000.
_TELEGRAM_MAX_RATIO = 19.5          # Bot API hard limit is 20.
_TELEGRAM_MAX_BYTES = 9_500_000     # Bot API hard limit is 10 MB.

_SCREENSHOT_HIDE_CSS = '''
  .new-nav,
  .new-footer,
  footer,
  .new-login-modal,
  .new-rules-modal,
  .new-18plus-modal,
  [data-login-open],
  .new-page-actions,
  .new-section-header,
  .new-ui--section {
    display: none !important;
  }
  html, body, main, .new-wrap {
    margin: 0 !important;
    padding: 8px !important;
    background: var(--surface, #fff) !important;
    width: max-content !important;
    max-width: none !important;
    min-height: 0 !important;
    height: auto !important;
    overflow: visible !important;
  }
  .new-results-fullbleed {
    margin: 0 !important;
    padding: 0 !important;
    width: max-content !important;
    display: block !important;
  }
  .new-results-wrap {
    overflow: visible !important;
    overflow-x: visible !important;
    overflow-y: visible !important;
    width: max-content !important;
    max-width: none !important;
    max-height: none !important;
    margin: 0 !important;
    isolation: auto !important;
  }
  .new-results-table {
    width: max-content !important;
  }
  .new-results-table th,
  .new-results-table td {
    position: static !important;
    inset: auto !important;
  }
  .new-results-table .is-sticky-left-2.col-team {
    width: auto !important;
    min-width: var(--results-col-team) !important;
    max-width: none !important;
    overflow: visible !important;
    text-overflow: clip !important;
  }
'''

_COMPACT_RESULTS_CSS = '''
  .new-results-table thead tr:nth-child(2),
  .new-results-table thead tr th:nth-child(n+5),
  .new-results-table tbody td,
  .new-results-table tbody tr:nth-child(n+21) {
    display: none !important;
  }
'''

_EXPAND_OVERFLOW_JS = """() => {
  const wrap = document.querySelector('.new-results-wrap');
  const table = document.querySelector('.new-results-table');
  const nodes = [
    document.documentElement,
    document.body,
    document.querySelector('main'),
    document.querySelector('.new-wrap'),
    document.querySelector('.new-results-fullbleed'),
    wrap,
    table,
  ];
  for (const node of nodes) {
    if (!node) continue;
    node.style.setProperty('overflow', 'visible', 'important');
    node.style.setProperty('overflow-x', 'visible', 'important');
    node.style.setProperty('overflow-y', 'visible', 'important');
    node.style.setProperty('max-height', 'none', 'important');
    node.style.setProperty('max-width', 'none', 'important');
    node.style.setProperty('width', 'max-content', 'important');
    node.style.setProperty('height', 'auto', 'important');
    node.style.setProperty('min-height', '0', 'important');
  }
  if (wrap) {
    wrap.style.setProperty('margin', '0', 'important');
    wrap.style.setProperty('isolation', 'auto', 'important');
  }
}"""

_WRAP_FITS_TABLE_JS = """() => {
  const wrap = document.querySelector('.new-results-wrap');
  const table = document.querySelector('.new-results-table');
  if (!wrap || !table) return false;
  const wr = wrap.getBoundingClientRect();
  return wr.width + 2 >= table.scrollWidth && wr.height + 2 >= table.scrollHeight;
}"""

_VIEWPORT_METRICS_JS = """wrap => {
  const table = wrap.querySelector('.new-results-table') || wrap;
  const wrapRect = wrap.getBoundingClientRect();
  const tableRect = table.getBoundingClientRect();
  const contentWidth = Math.max(
    wrap.scrollWidth, table.scrollWidth, wrapRect.width, tableRect.width,
  );
  const contentHeight = Math.max(
    wrap.scrollHeight, table.scrollHeight, wrapRect.height, tableRect.height,
  );
  return {
    width: Math.ceil(Math.max(
      contentWidth, wrapRect.left + contentWidth, tableRect.left + contentWidth,
    )),
    height: Math.ceil(Math.max(
      contentHeight, wrapRect.top + contentHeight, tableRect.top + contentHeight,
    )),
  };
}"""

_WRAP_CLIP_JS = """wrap => {
  const r = wrap.getBoundingClientRect();
  return {
    x: Math.max(0, Math.floor(r.x + window.scrollX)),
    y: Math.max(0, Math.floor(r.y + window.scrollY)),
    width: Math.ceil(Math.max(wrap.scrollWidth, r.width)),
    height: Math.ceil(Math.max(wrap.scrollHeight, r.height)),
  };
}"""


def _expanded_table_viewport(page, *, minimum_width: int) -> dict[str, int]:
    """Measure the unscrolled table and return a viewport large enough for it."""
    metrics = page.locator('.new-results-wrap').first.evaluate(_VIEWPORT_METRICS_JS)
    pad = 48
    return {
        'width': max(minimum_width, min(int(metrics['width']) + pad, _CHROMIUM_MAX_CSS_SIDE)),
        'height': max(800, min(int(metrics['height']) + pad, _CHROMIUM_MAX_CSS_SIDE)),
    }


def _fit_telegram_photo_png(image_bytes: bytes) -> bytes:
    """Keep a PNG inside Telegram sendPhoto size, dimensions, and ratio limits."""
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    width, height = image.size

    if width / max(height, 1) > _TELEGRAM_MAX_RATIO:
        target_height = max(height, int(width / _TELEGRAM_MAX_RATIO + 0.999))
        canvas = Image.new('RGB', (width, target_height), (255, 255, 255))
        canvas.paste(image, (0, (target_height - height) // 2))
        image = canvas
    elif height / max(width, 1) > _TELEGRAM_MAX_RATIO:
        target_width = max(width, int(height / _TELEGRAM_MAX_RATIO + 0.999))
        canvas = Image.new('RGB', (target_width, height), (255, 255, 255))
        canvas.paste(image, ((target_width - width) // 2, 0))
        image = canvas

    width, height = image.size
    if width + height > _TELEGRAM_MAX_DIMENSION_SUM:
        scale = _TELEGRAM_MAX_DIMENSION_SUM / float(width + height)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    while True:
        out = io.BytesIO()
        image.save(out, format='PNG', optimize=True)
        data = out.getvalue()
        if len(data) <= _TELEGRAM_MAX_BYTES:
            return data
        width, height = image.size
        if width <= 320 and height <= 320:
            raise RuntimeError('Results screenshot cannot be reduced below Telegram file limit')
        image = image.resize(
            (max(1, int(width * 0.85)), max(1, int(height * 0.85))),
            Image.Resampling.LANCZOS,
        )


def _validate_table_capture(raw: bytes, table_metrics: dict, *, scale_factor: int) -> None:
    """Reject a seemingly valid PNG when it is smaller than the full DOM table."""
    from PIL import Image

    captured = Image.open(io.BytesIO(raw))
    expected_width = int(table_metrics['width']) * scale_factor
    expected_height = int(table_metrics['height']) * scale_factor
    if captured.width + 4 < expected_width or captured.height + 4 < expected_height:
        raise RuntimeError(
            'Results screenshot is clipped: captured={}x{}, table={}x{}'.format(
                captured.width, captured.height, expected_width, expected_height,
            )
        )


def _prepare_results_table_for_screenshot(page, *, compact: bool = False) -> None:
    page.add_style_tag(content=_SCREENSHOT_HIDE_CSS)
    if compact:
        page.add_style_tag(content=_COMPACT_RESULTS_CSS)
    page.evaluate(_EXPAND_OVERFLOW_JS)
    page.locator('.new-results-table').first.wait_for(state='visible', timeout=10000)
    page.locator('.new-results-wrap').first.wait_for(state='visible', timeout=10000)


def _capture_expanded_results_png(
    page,
    *,
    viewport_width: int,
    scale_factor: int = _DEVICE_SCALE_FACTOR,
) -> bytes:
    """Capture the whole results wrap, including parts that normally live behind scroll."""
    page.evaluate(_EXPAND_OVERFLOW_JS)
    wrapper = page.locator('.new-results-wrap').first
    table = page.locator('.new-results-table').first
    page.set_viewport_size(_expanded_table_viewport(page, minimum_width=viewport_width))
    page.wait_for_function(_WRAP_FITS_TABLE_JS, timeout=5000)
    page.evaluate('() => window.scrollTo(0, 0)')
    page.set_viewport_size(_expanded_table_viewport(page, minimum_width=viewport_width))

    clip = wrapper.evaluate(_WRAP_CLIP_JS)
    table_metrics = table.evaluate(
        """node => ({
            width: Math.ceil(Math.max(node.scrollWidth, node.getBoundingClientRect().width)),
            height: Math.ceil(Math.max(node.scrollHeight, node.getBoundingClientRect().height))
        })"""
    )
    if int(clip['width']) < 1 or int(clip['height']) < 1:
        raise RuntimeError('Results screenshot clip is empty')

    screenshot_scale = 'device'
    used_scale = scale_factor
    max_side = max(int(clip['width']), int(clip['height']))
    if max_side * scale_factor > _CHROMIUM_MAX_CSS_SIDE:
        screenshot_scale = 'css'
        used_scale = 1

    raw = page.screenshot(
        type='png',
        full_page=True,
        clip={
            'x': float(clip['x']),
            'y': float(clip['y']),
            'width': float(clip['width']),
            'height': float(clip['height']),
        },
        animations='disabled',
        scale=screenshot_scale,
    )
    _validate_table_capture(raw, table_metrics, scale_factor=used_scale)
    return raw


def screenshot_tournament_results_png(
    game,
    *,
    url: str | None = None,
    viewport_width: int = 1400,
    compact: bool = False,
) -> bytes:
    """
    Headless Chromium screenshot of /games/<id>/tournament-results/.
    Targets the results table; adds a 20px white frame.
    """
    from playwright.sync_api import sync_playwright

    from games.telegram.ladder_image import _ensure_playwright_browsers_path

    _ensure_playwright_browsers_path()
    target = url or game_tournament_results_url(game)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={'width': viewport_width, 'height': 1800},
                device_scale_factor=_DEVICE_SCALE_FACTOR,
                color_scheme='light',
            )
            page.goto(target, wait_until='networkidle', timeout=60000)
            confirm = page.locator('[data-age-gate-confirm]')
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
                page.wait_for_timeout(200)
            _prepare_results_table_for_screenshot(page, compact=compact)
            raw = _capture_expanded_results_png(
                page,
                viewport_width=viewport_width,
                scale_factor=_DEVICE_SCALE_FACTOR,
            )
            return _add_white_frame(raw, pad_px=20)
        finally:
            browser.close()


def render_tournament_results_png(game) -> bytes | None:
    """Prefer a live site screenshot; return None if Playwright/Chromium fails."""
    prefer_screenshot = getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True)
    if not prefer_screenshot:
        return None
    try:
        png = screenshot_tournament_results_png(game)
        if png and png.startswith(b'\x89PNG'):
            return _fit_telegram_photo_png(png)
    except Exception:
        logger.exception(
            'Tournament results screenshot failed (%s)',
            game_tournament_results_url(game),
        )
    return None


def render_tournament_results_social_png(game) -> bytes | None:
    """Compact standings-only image for X and Instagram (top 20 rows)."""
    prefer_screenshot = getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True)
    if not prefer_screenshot:
        return None
    try:
        png = screenshot_tournament_results_png(game, compact=True)
        if png and png.startswith(b'\x89PNG'):
            return png
    except Exception:
        logger.exception(
            'Compact tournament results screenshot failed (%s)',
            game_tournament_results_url(game),
        )
    return None
