from __future__ import annotations

import traceback
from html import escape


def describe_render_failure(kind: str, url: str, exc: BaseException) -> str:
    """Keep enough render context in the queue row after cron logs rotate."""
    details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    message = (
        '{kind} screenshot render failed\n'
        'renderer=playwright\n'
        'fallback_to_pillow=False\n'
        'url={url}\n'
        'exception={exception}: {error}\n'
        'traceback:\n{traceback}'
    ).format(
        kind=kind,
        url=url,
        exception=exc.__class__.__name__,
        error=str(exc) or repr(exc),
        traceback=details,
    )
    return message[:8000]


def admin_render_failure_message(details: str) -> str:
    """Format a bounded HTML alert for the configured Telegram admin chat."""
    return '⚠️ <b>Ошибка создания Telegram-поста</b>\n<pre>{}</pre>'.format(
        escape(details[:3500]),
    )


def describe_post_failure(kind: str, stage: str, exc: BaseException) -> str:
    """Keep preparation/publish failures in the queue row and admin alert."""
    details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    message = (
        '{kind} Telegram post failed\n'
        'stage={stage}\n'
        'exception={exception}: {error}\n'
        'traceback:\n{traceback}'
    ).format(
        kind=kind,
        stage=stage,
        exception=exc.__class__.__name__,
        error=str(exc) or repr(exc),
        traceback=details,
    )
    return message[:8000]


def admin_post_failure_message(details: str) -> str:
    """Format a bounded HTML alert for any daily Telegram post failure."""
    return '⚠️ <b>Ошибка создания Telegram-поста</b>\n<pre>{}</pre>'.format(
        escape(details[:3500]),
    )
