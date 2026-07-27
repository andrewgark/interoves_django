"""Screenshot of a game list card for social posts."""

from __future__ import annotations

import logging

from django.conf import settings

from games.telegram.game_urls import admin_url, game_play_path, game_site_url
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
  .new-games-list-footer,
  .new-heading {
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
  .new-games-list-wrap {
    display: block !important;
  }
'''


def games_list_url(game) -> str:
    """URL of the folder games list that contains this game's card."""
    project_id = getattr(game, 'project_id', None)
    if project_id is None:
        project = getattr(game, 'project', None)
        project_id = getattr(project, 'id', None)
    if project_id and project_id not in ('main', 'sections'):
        return admin_url('/{}/games/'.format(project_id))
    return admin_url('/games/')


def _card_selector(game) -> str:
    # Card contains links like /games/<id>/ or /proj/games/<id>/.
    path = game_play_path(game).rstrip('/')
    return '.new-game-card:has(a[href*="{}"])'.format(path)


def _find_game_card(page, game, *, max_pages: int = 30):
    """Return locator for the game card, loading infinite-scroll pages if needed."""
    selector = _card_selector(game)
    card = page.locator(selector).first
    if card.count():
        return card

    list_el = page.locator('#newGamesList, .new-games-list').first
    if list_el.count() == 0:
        return None

    total = int(list_el.get_attribute('data-total-games') or '0')
    per_page = int(list_el.get_attribute('data-games-per-page') or '20')
    if per_page <= 0:
        per_page = 20
    total_pages = max(1, (total + per_page - 1) // per_page) if total else max_pages
    total_pages = min(total_pages, max_pages)

    list_path = page.url.split('?')[0]
    for page_num in range(2, total_pages + 1):
        page.evaluate(
            '''async ({listPath, pageNum}) => {
              const list = document.getElementById('newGamesList')
                || document.querySelector('.new-games-list');
              if (!list) return;
              const r = await fetch(listPath + '?page=' + pageNum, {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
              });
              const data = await r.json();
              if (!data.games_html) return;
              const tmp = document.createElement('div');
              tmp.innerHTML = data.games_html;
              while (tmp.firstChild) list.appendChild(tmp.firstChild);
            }''',
            {'listPath': list_path, 'pageNum': page_num},
        )
        page.wait_for_timeout(100)
        card = page.locator(selector).first
        if card.count():
            return card
    return None


def screenshot_game_announce_png(game, *, url: str | None = None, viewport_width: int = 900) -> bytes:
    """
    Headless Chromium screenshot of the game's `.new-game-card` on the games list.
    Adds a 20px white frame.
    """
    from playwright.sync_api import sync_playwright

    from games.telegram.ladder_image import _ensure_playwright_browsers_path

    _ensure_playwright_browsers_path()
    target = url or games_list_url(game)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={'width': viewport_width, 'height': 1400},
                device_scale_factor=2,
            )
            page.goto(target, wait_until='networkidle', timeout=60000)
            confirm = page.locator('[data-age-gate-confirm]')
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
                page.wait_for_timeout(200)
            page.add_style_tag(content=_SCREENSHOT_HIDE_CSS)
            page.wait_for_timeout(150)

            card = _find_game_card(page, game)
            if card is None or card.count() == 0:
                raise RuntimeError(
                    'Game card not found on {} for game {}'.format(target, game.id),
                )
            card.wait_for(state='visible', timeout=10000)
            try:
                page.wait_for_function(
                    '''(sel) => {
                      const img = document.querySelector(sel + ' img');
                      if (!img) return true;
                      return img.complete && img.naturalWidth > 0;
                    }''',
                    arg=_card_selector(game),
                    timeout=15000,
                )
            except Exception:
                pass
            raw = card.screenshot(type='png')
            return _add_white_frame(raw, pad_px=20)
        finally:
            browser.close()


def render_game_announce_png(game) -> bytes | None:
    """Prefer a live site screenshot of the list card; return None on failure."""
    prefer_screenshot = getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True)
    if not prefer_screenshot:
        return None
    try:
        png = screenshot_game_announce_png(game)
        if png and png.startswith(b'\x89PNG'):
            return png
    except Exception:
        logger.exception(
            'Game list card screenshot failed (%s) for %s',
            games_list_url(game),
            game_site_url(game),
        )
    return None
