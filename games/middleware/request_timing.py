"""Log slow game submission endpoints (send_attempt, alphabetty guess)."""

from __future__ import annotations

import logging
import re
import time

from django.conf import settings

logger = logging.getLogger('interoves.request_timing')

_ALPHABETTY_GUESS = re.compile(r'^/alphabetty/\d+/guess/?$')


def _watch_path(path: str) -> bool:
    if path.startswith('/send_attempt/'):
        return True
    return bool(_ALPHABETTY_GUESS.match(path))


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.slow_ms = float(getattr(settings, 'REQUEST_TIMING_SLOW_MS', 2000))

    def __call__(self, request):
        path = request.path
        if not _watch_path(path):
            return self.get_response(request)

        t0 = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if elapsed_ms >= self.slow_ms:
            logger.warning(
                'slow_request path=%s method=%s status=%s duration_ms=%.0f',
                path,
                request.method,
                getattr(response, 'status_code', '?'),
                elapsed_ms,
            )
        return response
