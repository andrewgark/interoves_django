"""Send daily share-card previews to the existing Telegram admin bot."""

from __future__ import annotations

import logging
from typing import Callable

from django.conf import settings

from games.daily_share_card import (
    CARD_HEIGHT,
    CARD_WIDTH,
    RENDERER_VERSION,
    synthetic_preview_payloads,
)
from games.daily_share_card_render import assert_share_card_png, render_share_card_png
from games.telegram.api import send_message, send_photo
from games.telegram.config import admin_chat_id, telegram_admin_configured

logger = logging.getLogger('application')


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
    if token:
        text = text.replace(token, '[redacted]')
    return text


def _caption(payload: dict, *, kind_note: str = '') -> str:
    parts = [
        '<b>Share card preview</b>',
        'game: <code>{}</code>'.format(payload.get('game_kind') or payload.get('kind')),
        'daily: <code>{}</code>'.format(payload.get('number') or '—'),
        'locale: <code>{}</code>'.format(payload.get('locale') or 'ru'),
        'size: {}×{}'.format(CARD_WIDTH, CARD_HEIGHT),
        'renderer: v{}'.format(payload.get('renderer_version') or RENDERER_VERSION),
        'source: {}'.format('synthetic' if payload.get('synthetic') else 'live'),
        'headline: {}'.format(payload.get('headline') or ''),
    ]
    if kind_note:
        parts.append(kind_note)
    return '\n'.join(parts)


def _send_png(png: bytes, *, filename: str, caption: str) -> dict:
    result = send_photo(
        admin_chat_id(),
        png,
        caption=caption,
        filename=filename,
    )
    if result is None:
        raise RuntimeError('Telegram sendPhoto failed')
    return result


def send_social_teasers(send_fn: Callable | None = None) -> list[str]:
    """Original social-media renders, for visual comparison with share cards."""
    notes = []
    sender = send_fn or _send_png
    try:
        from games.telegram.ladder_channel import resolve_today_ladder
        from games.telegram.ladder_image import render_ladder_teaser_png

        ladder = resolve_today_ladder()
        if ladder is not None:
            png = render_ladder_teaser_png(
                ladder.task,
                ladder_number=ladder.number,
                fallback_to_pillow=True,
            )
            sender(
                png,
                filename='ladder-social-{}.png'.format(ladder.number),
                caption=(
                    '<b>Existing social render</b>\n'
                    'game: <code>ladder</code>\n'
                    'daily: <code>{}</code>\n'
                    'source: current teaser renderer'
                ).format(ladder.number),
            )
            notes.append('ladder teaser #{}'.format(ladder.number))
    except Exception:
        logger.exception('Ladder social teaser preview failed')
        notes.append('ladder teaser failed')
    try:
        from games.telegram.word_salad_channel import resolve_today_salad
        from games.telegram.word_salad_image import render_word_salad_teaser_png

        salad = resolve_today_salad()
        if salad is not None:
            png = render_word_salad_teaser_png(
                salad.task,
                salad_number=salad.number,
                fallback_to_pillow=True,
            )
            sender(
                png,
                filename='salad-social-{}.png'.format(salad.number),
                caption=(
                    '<b>Existing social render</b>\n'
                    'game: <code>salad</code>\n'
                    'daily: <code>{}</code>\n'
                    'source: current teaser renderer'
                ).format(salad.number),
            )
            notes.append('salad teaser #{}'.format(salad.number))
    except Exception:
        logger.exception('Salad social teaser preview failed')
        notes.append('salad teaser failed')
    return notes


def preview_daily_share_cards(
    *,
    include_social: bool = True,
    render_fn: Callable | None = None,
    send_fn: Callable | None = None,
    intro_fn: Callable | None = None,
) -> tuple[bool, str]:
    if not telegram_admin_configured():
        return False, 'TELEGRAM_ADMIN_CHAT_ID / bot token not configured'

    renderer = render_fn or render_share_card_png
    sender = send_fn or _send_png
    intro = intro_fn or (lambda text: send_message(admin_chat_id(), text))

    if render_fn is None:
        from games.daily_share_card_render import _prepare_playwright_env
        _prepare_playwright_env()

    try:
        intro(
            '<b>Daily share-card visual QA</b>\n'
            'Synthetic player results through the production JS renderer.\n'
            'Production deploy is not part of this preview.'
        )
        notes = []
        if include_social:
            notes.extend(send_social_teasers(sender))
        sent = 0
        for payload in synthetic_preview_payloads():
            png = renderer(payload)
            assert_share_card_png(png)
            sender(
                png,
                filename=payload.get('filename') or 'share-card.png',
                caption=_caption(payload),
            )
            sent += 1
            notes.append(
                '{} {} {}'.format(
                    payload.get('kind'),
                    payload.get('locale'),
                    payload.get('headline_style') or '',
                ).strip()
            )
        return True, 'Sent {} share cards ({})'.format(sent, ', '.join(notes))
    except Exception as exc:
        logger.error('Daily share-card preview failed: %s', _safe_error(exc))
        return False, 'Preview failed: {}'.format(_safe_error(exc))
