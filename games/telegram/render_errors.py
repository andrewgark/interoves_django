from __future__ import annotations

import traceback


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
