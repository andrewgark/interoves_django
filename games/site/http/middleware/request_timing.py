"""Log slow gameplay endpoints and their lock-sensitive phases."""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from contextlib import contextmanager

from django.conf import settings
from django.db import connection

logger = logging.getLogger('interoves.request_timing')

_ALPHABETTY_GUESS = re.compile(r'^/alphabetty/[^/]+/guess/?$')
# Task numbers and permanent share hashes both occupy one URL segment. Censorly
# random games have an extra ``r/`` segment, so include that route explicitly.
_DAILY_TIMING = re.compile(
    r'^/(?:ladder|salad|alphabetty|replacements|censorly)/(?:r/)?[^/]+/timing/?$'
)
_GENERIC_GAME_TIMING = re.compile(
    r'^/(?:[a-zA-Z0-9_-]+/)?games/[a-zA-Z0-9_]+/[^/]+/timing/?$'
)
_RADDLE_UI = re.compile(r'^/send_raddle_ui/\d+/?$')
_CENSORLY_RANDOM = re.compile(r'^/censorly/random/?$')
_CENSORLY_GUESS = re.compile(r'^/censorly/(?:r/[^/]+|[^/]+)/guess/?$')
_CENSORLY_PAGE = re.compile(r'^/censorly/(?:r/[^/]+|[^/]+)/?$')


def _watch_path(path: str) -> bool:
    # The hub is the most visited page and the most frequent slow request.
    if path == '/' or path.startswith('/send_attempt/'):
        return True
    return bool(
        _ALPHABETTY_GUESS.match(path)
        or _DAILY_TIMING.match(path)
        or _GENERIC_GAME_TIMING.match(path)
        or _RADDLE_UI.match(path)
        or _CENSORLY_RANDOM.match(path)
        or _CENSORLY_GUESS.match(path)
        or _CENSORLY_PAGE.match(path)
    )


@contextmanager
def timing_phase(request, name: str):
    """Accumulate a named phase for watched requests without logging payloads."""
    phases = getattr(request, '_interoves_timing_phases', None)
    if phases is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        phases[name] = phases.get(name, 0.0) + (time.perf_counter() - started) * 1000.0


class _SqlTally:
    """Count and time this request's queries, to tell DB wait from Python."""

    def __init__(self):
        self.count = 0
        self.ms = 0.0

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.count += 1
            self.ms += (time.perf_counter() - started) * 1000.0


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.slow_ms = float(getattr(settings, 'REQUEST_TIMING_SLOW_MS', 2000))

    def __call__(self, request):
        path = request.path
        watched_path = _watch_path(path)
        request._interoves_timing_phases = {} if watched_path else None
        t0 = time.perf_counter()
        request._interoves_started_at = t0
        response = None
        sql = _SqlTally() if watched_path else None
        try:
            if sql is None:
                response = self.get_response(request)
            else:
                with connection.execute_wrapper(sql):
                    response = self.get_response(request)
            return response
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            request._interoves_elapsed_ms = elapsed_ms
            if elapsed_ms >= self.slow_ms:
                resolver_match = getattr(request, 'resolver_match', None)
                endpoint = (
                    getattr(resolver_match, 'route', None)
                    or getattr(resolver_match, 'view_name', None)
                    or 'unresolved'
                )
                phases = request._interoves_timing_phases or {}
                phases_total_ms = sum(phases.values())
                unaccounted_ms = max(0.0, elapsed_ms - phases_total_ms)
                phase_summary = ','.join(
                    '{}:{:.0f}'.format(name, duration_ms)
                    for name, duration_ms in phases.items()
                ) or '-'
                # SQL time overlaps the phases, so it is reported beside them
                # rather than counted into unaccounted_ms.
                sql_summary = (
                    'sql_count={} sql_ms={:.0f}'.format(sql.count, sql.ms)
                    if sql is not None else 'sql_count=- sql_ms=-'
                )
                logger.warning(
                    'slow_request request_id=%s endpoint=%s method=%s status=%s '
                    'duration_ms=%.0f unaccounted_ms=%.0f instance=%s pid=%s phases=%s %s',
                    getattr(request, 'interoves_request_id', 'unavailable'),
                    endpoint,
                    request.method,
                    getattr(response, 'status_code', 'exception'),
                    elapsed_ms,
                    unaccounted_ms,
                    getattr(settings, 'INSTANCE_ID', '') or socket.gethostname() or 'unknown',
                    os.getpid(),
                    phase_summary,
                    sql_summary,
                )
