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
