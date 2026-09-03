"""Rasterize daily share-card SVGs with the production JavaScript renderer."""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from games.daily_share_card import CARD_HEIGHT, CARD_WIDTH, RENDERER_VERSION
from games.telegram.ladder_image import _ensure_playwright_browsers_path

logger = logging.getLogger('application')

_JS_NAME = 'daily_share_card.js'


def renderer_js_path() -> Path:
    return Path(settings.BASE_DIR) / 'static' / 'js' / _JS_NAME


@lru_cache(maxsize=1)
def _renderer_js() -> str:
    return renderer_js_path().read_text(encoding='utf-8')


def render_share_card_png(payload: dict) -> bytes:
    """
    Run the frontend DailyShareCard renderer in headless Chromium.

    This is the same SVG → canvas → PNG path the browser uses for Copy Image
    and Web Share. Do not substitute a separate Pillow mock here.
    """
    from playwright.sync_api import sync_playwright

    _ensure_playwright_browsers_path()
    script = _renderer_js()
    payload_json = json.dumps(payload, ensure_ascii=False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={'width': CARD_WIDTH, 'height': CARD_HEIGHT},
                device_scale_factor=1,
            )
            page.set_content(
                '<!doctype html><html><head><meta charset="utf-8"></head>'
                '<body></body></html>',
                wait_until='domcontentloaded',
            )
            page.add_script_tag(content=script)
            b64 = page.evaluate(
                """async (payload) => {
                    const api = window.DailyShareCard;
                    if (!api || typeof api.renderShareCardPng !== 'function') {
                        throw new Error('DailyShareCard missing');
                    }
                    const blob = await api.renderShareCardPng(payload);
                    const buf = await blob.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    const chunk = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunk) {
                        binary += String.fromCharCode.apply(
                            null,
                            bytes.subarray(i, i + chunk)
                        );
                    }
                    return btoa(binary);
                }""",
                json.loads(payload_json),
            )
        finally:
            browser.close()
    if not b64:
        raise RuntimeError('Share card renderer returned empty PNG')
    png = base64.b64decode(b64)
    if not png.startswith(b'\x89PNG'):
        raise RuntimeError('Share card renderer did not return a PNG')
    return png


def png_dimensions(png: bytes) -> tuple[int, int]:
    from PIL import Image
    import io

    image = Image.open(io.BytesIO(png))
    return image.size


def assert_share_card_png(png: bytes) -> tuple[int, int]:
    width, height = png_dimensions(png)
    if (width, height) != (CARD_WIDTH, CARD_HEIGHT):
        raise RuntimeError(
            'Share card PNG is {}x{}, expected {}x{}'.format(
                width, height, CARD_WIDTH, CARD_HEIGHT,
            )
        )
    return width, height
