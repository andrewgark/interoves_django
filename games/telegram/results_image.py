"""Screenshot of tournament results table for Telegram /des_results."""

from __future__ import annotations

import logging

from django.conf import settings

from games.telegram.game_urls import game_tournament_results_url
from games.telegram.ladder_image import _add_white_frame

logger = logging.getLogger('application')

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
  .new-ui--section > p,
  .new-ui--section > .new-card {
    display: none !important;
  }
  body, .new-wrap {
    margin: 0 !important;
    padding: 0.75rem !important;
    background: var(--surface, #fff) !important;
  }
  html, body {
    overflow: visible !important;
  }
  .new-results-fullbleed {
    margin: 0 !important;
  }
'''


def screenshot_tournament_results_png(game, *, url: str | None = None, viewport_width: int = 1400) -> bytes:
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
                device_scale_factor=2,
            )
            page.goto(target, wait_until='networkidle', timeout=60000)
            confirm = page.locator('[data-age-gate-confirm]')
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
                page.wait_for_timeout(200)
            page.add_style_tag(content=_SCREENSHOT_HIDE_CSS)
            page.wait_for_timeout(150)

            raw = None
            for selector in ('.new-results-wrap', '.new-results-fullbleed', '.new-results-table', 'main'):
                loc = page.locator(selector).first
                if loc.count() == 0:
                    continue
                try:
                    loc.wait_for(state='visible', timeout=10000)
                    raw = loc.screenshot(type='png')
                    break
                except Exception:
                    continue
            if raw is None:
                raw = page.screenshot(type='png', full_page=True)
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
            return png
    except Exception:
        logger.exception(
            'Tournament results screenshot failed (%s)',
            game_tournament_results_url(game),
        )
    return None
