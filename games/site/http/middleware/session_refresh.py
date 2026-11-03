"""Sliding expiry for authenticated sessions, refreshed at most once a day.

Django only re-saves a session (and re-issues its cookie with a fresh
``Max-Age``) when the session data changes, so without this a session expires
``SESSION_COOKIE_AGE`` after login no matter how active the player is.
``SESSION_SAVE_EVERY_REQUEST`` would fix that at the cost of a ``django_session``
write on every timing ping and attempt; this middleware touches the session
only when the stored refresh stamp is older than ``SESSION_REFRESH_INTERVAL``.
"""

import time

from django.conf import settings

from games.site.http.middleware.request_timing import timing_phase

SESSION_REFRESHED_AT_KEY = '_interoves_session_refreshed_at'

# Gameplay POSTs may write their own session keys concurrently; refreshing only
# on page loads keeps a stale full-session save from clobbering those writes.
_REFRESH_METHODS = frozenset({'GET', 'HEAD'})


def _needs_refresh(session, now: int) -> bool:
    try:
        refreshed_at = int(session.get(SESSION_REFRESHED_AT_KEY) or 0)
    except (TypeError, ValueError):
        refreshed_at = 0
    return now - refreshed_at >= settings.SESSION_REFRESH_INTERVAL


class SlidingSessionRefreshMiddleware:
    """Extend an authenticated session's expiry while the player keeps visiting."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, 'method', '') not in _REFRESH_METHODS:
            return response
        if getattr(response, 'status_code', 500) >= 500:
            return response
        user = getattr(request, 'user', None)
        if not getattr(user, 'is_authenticated', False):
            return response
        session = getattr(request, 'session', None)
        if session is None:
            return response
        with timing_phase(request, 'session_refresh'):
            now = int(time.time())
            if _needs_refresh(session, now):
                # Marks the session modified; SessionMiddleware then saves it
                # with a new expire_date and re-sends the cookie.
                session[SESSION_REFRESHED_AT_KEY] = now
        return response
