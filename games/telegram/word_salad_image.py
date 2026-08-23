"""PNG teaser of today's Word Salad for Telegram — prefers a real site screenshot."""

from __future__ import annotations

import io
import logging

from django.conf import settings
from PIL import Image, ImageDraw

from games.section_paths import section_last_path
from games.telegram.game_urls import site_base_url
from games.telegram.ladder_image import (
    _load_font,
    screenshot_page_element_png,
)
from games.word_salad import (
    WORD_SALAD_GAME_ID,
    build_ui_context,
    default_state,
    mask_for_word,
    parse_task_data,
)

logger = logging.getLogger('application')

WORD_SALAD_LAST_PATH = section_last_path(WORD_SALAD_GAME_ID)
_WORD_SALAD_SELECTORS = (
    '.new-word-salad',
    'main.new-wrap',
    'main',
)


def word_salad_last_screenshot_url() -> str:
    return '{}{}'.format(site_base_url().rstrip('/'), WORD_SALAD_LAST_PATH)


def screenshot_word_salad_last_png(*, url: str | None = None, viewport_width: int = 1100) -> bytes:
    target = url or word_salad_last_screenshot_url()
    return screenshot_page_element_png(
        target,
        _WORD_SALAD_SELECTORS,
        viewport_width=viewport_width,
    )


def render_word_salad_teaser_png_pillow(task, *, salad_number: int | str | None = None) -> bytes:
    """Fallback schematic: 4×4 grid + masked word list."""
    grid, words = parse_task_data(task.checker_data, task.answer)
    ui = build_ui_context(grid, words, default_state())
    theme = (getattr(task, 'text', None) or '').strip()

    font_title = _load_font(28, bold=True)
    font_sub = _load_font(18, bold=True)
    font_cell = _load_font(22, bold=True)
    font_word = _load_font(16)
    font_meta = _load_font(14)

    pad = 28
    cell = 52
    gap = 8
    grid_size = cell * 4 + gap * 3
    words_w = 220
    col_gap = 36
    width = pad * 2 + grid_size + col_gap + words_w
    header_h = 36 + (28 if theme else 0) + 16
    height = pad * 2 + header_h + max(grid_size, 22 + len(ui['words']) * 26)

    img = Image.new('RGB', (width, height), '#F7F4EF')
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 6), fill='#2F6F4E')

    y = pad + 4
    title = 'Салат №{}'.format(salad_number) if salad_number is not None else 'Салат'
    draw.text((pad, y), title, font=font_title, fill='#1A1A1A')
    y += 36
    if theme:
        draw.text((pad, y), theme, font=font_sub, fill='#2F6F4E')
        y += 28
    top = y + 8

    for row_index, row in enumerate(ui['grid_rows']):
        for col_index, item in enumerate(row):
            x0 = pad + col_index * (cell + gap)
            y0 = top + row_index * (cell + gap)
            draw.rounded_rectangle(
                (x0, y0, x0 + cell, y0 + cell),
                radius=8,
                fill='#FFFFFF',
                outline='#D7D0C6',
            )
            letter = item['letter']
            tw = draw.textlength(letter, font=font_cell)
            draw.text(
                (x0 + (cell - tw) / 2, y0 + 12),
                letter,
                font=font_cell,
                fill='#1A1A1A',
            )

    wx = pad + grid_size + col_gap
    wy = top
    draw.text((wx, wy), 'Ответы', font=font_meta, fill='#888888')
    wy += 22
    for word in ui['words']:
        mask = mask_for_word(word['original']).replace('⬜', '□')
        label = '{}  ({})'.format(mask, word['length'])
        draw.text((wx, wy), label, font=font_word, fill='#333333')
        wy += 26

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def render_word_salad_teaser_png(
    task,
    *,
    salad_number: int | str | None = None,
    url: str | None = None,
    fallback_to_pillow: bool = True,
) -> bytes:
    """Prefer a real screenshot of SITE_BASE_URL/salad/last/."""
    prefer_screenshot = getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True)
    if prefer_screenshot:
        try:
            png = screenshot_word_salad_last_png(url=url)
            if png and png.startswith(b'\x89PNG'):
                return png
        except Exception:
            shot_url = url or word_salad_last_screenshot_url()
            if not fallback_to_pillow:
                logger.exception(
                    'Salad screenshot failed (%s); refusing Pillow fallback',
                    shot_url,
                )
                raise
            logger.exception(
                'Salad screenshot failed (%s); falling back to Pillow',
                shot_url,
            )
    if not fallback_to_pillow:
        raise RuntimeError('Real salad screenshot is required for this render')
    return render_word_salad_teaser_png_pillow(task, salad_number=salad_number)
