"""Log slow game submission endpoints (send_attempt, alphabetty guess)."""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager

from django.conf import settings

logger = logging.getLogger('interoves.request_timing')

_ALPHABETTY_GUESS = re.compile(r'^/alphabetty/\d+/guess/?$')


def _watch_path(path: str) -> bool:
    if path.startswith('/send_attempt/'):
        return True
    return bool(_ALPHABETTY_GUESS.match(path))


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


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.slow_ms = float(getattr(settings, 'REQUEST_TIMING_SLOW_MS', 2000))

    def __call__(self, request):
        path = request.path
        if not _watch_path(path):
            return self.get_response(request)

        request._interoves_timing_phases = {}
        t0 = time.perf_counter()
        response = None
        try:
            response = self.get_response(request)
            return response
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if elapsed_ms >= self.slow_ms:
                phases = ','.join(
                    '{}:{:.0f}'.format(name, duration_ms)
                    for name, duration_ms in request._interoves_timing_phases.items()
                ) or '-'
                logger.warning(
                    'slow_request path=%s method=%s status=%s duration_ms=%.0f phases=%s',
                    path,
                    request.method,
                    getattr(response, 'status_code', 'exception'),
                    elapsed_ms,
                    phases,
                )
