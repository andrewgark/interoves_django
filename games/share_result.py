"""Shared Wordle-style share text: elapsed time and public links."""

from __future__ import annotations

from typing import Any, Optional

DEFAULT_SHARE_HOST = 'interoves.com'


def format_elapsed(seconds: int | None) -> str:
    """1ч 32м 44с / 32м 44с / 44с."""
    if seconds is None:
        seconds = 0
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return '{}ч {}м {}с'.format(hours, minutes, secs)
    if minutes:
        return '{}м {}с'.format(minutes, secs)
    return '{}с'.format(secs)


def format_elapsed_line(seconds: int | None) -> str:
    return '⏱️ {}'.format(format_elapsed(seconds))


def share_host_from_value(host: str | None, default: str = DEFAULT_SHARE_HOST) -> str:
    host = (host or default).split(':')[0] or default
    return host


def share_host_from_request(request, default: str = DEFAULT_SHARE_HOST) -> str:
    host = ''
    if request is not None:
        try:
            host = request.get_host() or ''
        except Exception:
            host = ''
    return share_host_from_value(host, default=default)


def share_path(path: str | None) -> str:
    return str(path or '').strip().lstrip('/').rstrip('/')


def format_share_link(host: str | None, path: str | None, default_host: str = DEFAULT_SHARE_HOST) -> str:
    return '🔗 {}/{}'.format(share_host_from_value(host, default=default_host), share_path(path))


def elapsed_seconds_from_attempts(attempts: Optional[list] = None) -> int:
    """Время от первой до последней посылки актёра."""
    times = [getattr(item, 'time', None) for item in (attempts or [])]
    times = [item for item in times if item is not None]
    if not times:
        return 0
    return max(0, int((max(times) - min(times)).total_seconds()))


def elapsed_label_from_attempts(attempts: Optional[list] = None) -> str:
    return format_elapsed(elapsed_seconds_from_attempts(attempts))


def format_archive_result_line(squares: str | None = None, elapsed_label: str | None = None) -> str:
    """One archive-list line: squares, then ⏱️ time (same order as the share card)."""
    parts = []
    if squares:
        parts.append(squares)
    if elapsed_label:
        parts.append('⏱️ {}'.format(elapsed_label))
    return '  '.join(parts)
