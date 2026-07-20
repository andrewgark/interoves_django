"""PNG teaser of today's raddle ladder for Telegram — prefers a real site screenshot."""

from __future__ import annotations

import io
import logging
import os
from functools import lru_cache

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from games.raddle import (
    build_raddle_ui_context,
    default_raddle_state,
    length_mask_display,
    parse_raddle_data,
)
from games.telegram.game_urls import site_base_url

logger = logging.getLogger('application')

# Geometric square works in DejaVu; emoji ◼️ often falls back to tofu.
_MASK_CHAR = '■'

_FONT_DIRS = (
    '/usr/share/fonts/truetype/dejavu',
    '/usr/share/fonts/TTF',
    '/usr/share/fonts/truetype',
)

# Public page that redirects to the latest published ladder.
LADDER_LAST_PATH = '/games/ladder/last/'

_SCREENSHOT_HIDE_CSS = '''
  .new-nav,
  .new-footer,
  footer,
  .new-login-modal,
  .new-rules-modal,
  .new-18plus-modal,
  [data-login-open],
  .new-task-group-nav,
  .new-page-actions {
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
  /* CSS squares — px so size is not crushed by raddle clamp()/cqi font-size. */
  .tg-shot-sq {
    display: inline-block;
    width: 14px;
    height: 14px;
    margin: 0 2px 0 0;
    background: #4b5563;
    border-radius: 2.5px;
    vertical-align: middle;
    flex-shrink: 0;
  }
  .tg-shot-fake-input {
    display: inline-flex !important;
    flex-wrap: nowrap;
    align-items: center;
    box-sizing: border-box;
    min-height: 36px;
    padding: 6px 10px !important;
    border: 1.5px solid #8b949e !important;
    border-radius: 5px !important;
    background: #ffffff !important;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.08);
    font: inherit;
    letter-spacing: normal !important;
    text-transform: none !important;
  }
  .new-raddle-line__mask {
    display: inline-flex;
    flex-wrap: nowrap;
    align-items: center;
    letter-spacing: normal !important;
    font-size: 1rem !important;
  }
'''


def _paint_mask_squares_for_screenshot(page) -> None:
    """
    Replace emoji/mask glyphs with CSS squares and turn playable inputs into
    bordered fake-inputs filled with the same squares (screenshot only).
    """
    page.evaluate(
        """() => {
          const isSq = (ch) => {
            const c = ch.codePointAt(0);
            return (
              c === 0x25FE || c === 0x25A0 || c === 0x25FC || c === 0x25AA ||
              c === 0x25AB || c === 0x2B1B || c === 0x2B1C || c === 0x25FB ||
              c === 0x25A1 || c === 0x25A3
            );
          };
          const fill = (node, raw) => {
            node.textContent = '';
            for (const ch of (raw || '')) {
              const c = ch.codePointAt(0);
              if (c === 0xFE0F || c === 0xFE0E) continue;
              if (isSq(ch)) {
                const s = document.createElement('span');
                s.className = 'tg-shot-sq';
                s.setAttribute('aria-hidden', 'true');
                node.appendChild(s);
              } else {
                node.appendChild(document.createTextNode(ch));
              }
            }
          };
          document.querySelectorAll('.new-raddle-line__mask').forEach((el) => {
            fill(el, el.textContent);
          });
          document.querySelectorAll('input.new-raddle-input').forEach((el) => {
            const fake = document.createElement('div');
            fake.className = (el.className || '') + ' tg-shot-fake-input';
            fake.style.maxWidth = '100%';
            fake.style.width = 'max-content';
            const cell = el.closest('.new-raddle-word-cell');
            if (cell) {
              const v = getComputedStyle(cell).getPropertyValue('--raddle-len');
              if (v) fake.style.setProperty('--raddle-len', v.trim());
            }
            fill(fake, el.getAttribute('placeholder') || el.placeholder || '');
            el.replaceWith(fake);
          });
        }"""
    )


@lru_cache(maxsize=8)
def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = []
    if bold:
        names.append('DejaVuSans-Bold.ttf')
    names.append('DejaVuSans.ttf')
    for directory in _FONT_DIRS:
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _mask_text(mask, word: str) -> str:
    raw = length_mask_display(mask, word).strip()
    return raw.replace('◼️', _MASK_CHAR)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or '').split()
    if not words:
        return ['']
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = '{} {}'.format(current, word)
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def ladder_last_screenshot_url() -> str:
    return '{}/games/ladder/last/'.format(site_base_url().rstrip('/'))


def screenshot_ladder_last_png(*, url: str | None = None, viewport_width: int = 1100) -> bytes:
    """
    Headless Chromium screenshot of the live ladder page (same look as the site).
    Targets `.new-raddle-task`; falls back to `.new-raddle-layout` / main content.
    Adds a 20px white frame around the crop.
    """
    from playwright.sync_api import sync_playwright

    target = url or ladder_last_screenshot_url()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={'width': viewport_width, 'height': 1600},
                device_scale_factor=2,
            )
            page.goto(target, wait_until='networkidle', timeout=60000)
            # Dismiss 18+ gate if present (ladder itself is not 18+, but modal may show).
            confirm = page.locator('[data-age-gate-confirm]')
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
                page.wait_for_timeout(200)
            page.add_style_tag(content=_SCREENSHOT_HIDE_CSS)
            _paint_mask_squares_for_screenshot(page)
            page.wait_for_timeout(150)

            raw = None
            for selector in ('.new-raddle-task', '.new-raddle-layout', 'main.new-wrap', 'main'):
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


def _add_white_frame(png_bytes: bytes, *, pad_px: int = 20) -> bytes:
    """Pad PNG with a solid white border on all sides."""
    img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    framed = Image.new('RGB', (img.width + pad_px * 2, img.height + pad_px * 2), '#FFFFFF')
    framed.paste(img, (pad_px, pad_px))
    out = io.BytesIO()
    framed.save(out, format='PNG', optimize=True)
    return out.getvalue()


def render_ladder_teaser_png_pillow(task, *, ladder_number: int | str | None = None) -> bytes:
    """Fallback schematic drawing when Playwright/Chromium is unavailable."""
    parsed = parse_raddle_data(task)
    ui = build_raddle_ui_context(parsed, default_raddle_state(parsed['n_words']))

    font_title = _load_font(28, bold=True)
    font_sub = _load_font(18, bold=True)
    font_word = _load_font(20, bold=True)
    font_mask = _load_font(16)
    font_clue = _load_font(15)
    font_meta = _load_font(14)

    pad = 28
    col_gap = 36
    left_w = 320
    right_w = 360
    width = pad * 2 + left_w + col_gap + right_w
    row_h = 28

    measure = Image.new('RGB', (10, 10), 'white')
    draw_m = ImageDraw.Draw(measure)

    title = 'Лесенка №{}'.format(ladder_number) if ladder_number is not None else 'Лесенка'
    subtitle = 'От {} до {}'.format(ui['title_from'], ui['title_to'])
    author = (getattr(task, 'tags', None) or {}).get('author') or ''

    clue_block_h = 22
    for hint in ui['unused_hints']:
        wrapped = _wrap(draw_m, str(hint['display']), font_clue, right_w - 24)
        clue_block_h += len(wrapped) * 20 + 6

    header_h = 36 + 28 + (22 if author else 0) + 30
    ladder_h = 22 + len(ui['rows']) * row_h
    height = max(420, pad * 2 + 6 + header_h + max(ladder_h, clue_block_h))

    img = Image.new('RGB', (width, height), '#F7F4EF')
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 6), fill='#2F6F4E')

    y = pad + 4
    draw.text((pad, y), title, font=font_title, fill='#1A1A1A')
    y += 36
    draw.text((pad, y), subtitle, font=font_sub, fill='#2F6F4E')
    y += 28
    if author:
        draw.text((pad, y), 'Автор: {}'.format(author), font=font_meta, fill='#666666')
        y += 22

    left_x = pad
    right_x = pad + left_w + col_gap
    top_cols = y + 8

    ly = top_cols
    draw.text((left_x, ly), 'Лестница', font=font_meta, fill='#888888')
    ly += 22
    for row in ui['rows']:
        if row['is_solved']:
            text = row['word']
            font = font_word
            fill = '#1A1A1A'
        else:
            text = _mask_text(parsed['masks'][row['index']], parsed['words'][row['index']])
            font = font_mask
            fill = '#555555'
        label = '({})'.format(row['length_label'])
        draw.text((left_x, ly), text, font=font, fill=fill)
        tw = draw.textlength(text, font=font)
        draw.text((left_x + tw + 8, ly + 2), label, font=font_meta, fill='#999999')
        ly += row_h

    cy = top_cols
    draw.text((right_x, cy), 'Подсказки, не по порядку', font=font_meta, fill='#888888')
    cy += 22
    for hint in ui['unused_hints']:
        lines = _wrap(draw, str(hint['display']), font_clue, right_w - 24)
        for i, line in enumerate(lines):
            prefix = '• ' if i == 0 else '  '
            draw.text((right_x, cy), prefix + line, font=font_clue, fill='#333333')
            cy += 20
            if cy > height - pad:
                break
        cy += 6
        if cy > height - pad:
            break

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def render_ladder_teaser_png(task, *, ladder_number: int | str | None = None) -> bytes:
    """
    Prefer a real screenshot of SITE_BASE_URL/games/ladder/last/;
    fall back to the Pillow schematic if Chromium/Playwright is missing.
    """
    prefer_screenshot = getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True)
    if prefer_screenshot:
        try:
            png = screenshot_ladder_last_png()
            if png and png.startswith(b'\x89PNG'):
                return png
        except Exception:
            logger.exception(
                'Ladder screenshot failed (%s); falling back to Pillow',
                ladder_last_screenshot_url(),
            )
    return render_ladder_teaser_png_pillow(task, ladder_number=ladder_number)
