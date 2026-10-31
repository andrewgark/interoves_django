"""interoves.com and ru.interoves.com: the menu switcher and the gentle hint.

Both hostnames serve the same application. The menu always offers both; a
small hint suggests the other one when the visitor's country (by IP) says it
may suit them better: ``ru`` for visitors from Russia on interoves.com,
``intl`` for everyone else on ru.interoves.com. Nothing ever redirects.

The visitor's decision lives in the ``interoves_site_version`` cookie, set by
static/js/site_version.js with ``Domain=.interoves.com`` so both hosts see it:
``chosen:<version>:<unix time>`` after a deliberate switch (no more hints for
a year) and ``stayed:<version>:<unix time>`` after "stay here" (30 days).
It holds nothing else and is never used for authentication.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qsl, urlencode

from django.utils.encoding import escape_uri_path

from games.site.http.visitor_country import INTL_HOST, RU_HOST, UNKNOWN, visitor_country

INTL = 'intl'
RU = 'ru'
VERSIONS = {INTL: INTL_HOST, RU: RU_HOST}
VERSION_BY_HOST = {host: version for version, host in VERSIONS.items()}

COOKIE_NAME = 'interoves_site_version'
CHOSEN_MAX_AGE = 365 * 86400
STAYED_MAX_AGE = 30 * 86400

# Routes whose URL must not move to the other host: OAuth and Telegram login
# (code/state live in this host's session), payments, subscription checkout
# and their returns, one-time links. Switching from them lands on the other
# host's home page, and the hint is never shown on them.
_SENSITIVE_PATH_RE = re.compile(
    r'^/(?:accounts|telegram|admin|logout|pay|donate|subscription|profile/merge|anon-merge-jobs'
    r'|yookassa|nowpayments|tribute)(?:/|$)'
    r'|/team/invite/'
    r'|/(?:confirm|reject)_user_joining_team/'
)
# Query parameters dropped even on ordinary pages: any name with one of these
# words as an underscore-separated part (code, access_token, payment_id, …).
# Redirect targets (next, return_to) are dropped too: never echo them into a link.
_SENSITIVE_PARAM_RE = re.compile(
    r'(?:^|_)(?:code|state|token|secret|key|sig|signature|hash|nonce|otp|password|auth|session'
    r'|payment|invoice|ticket|csrf|next|redirect|return)(?:_|$)', re.IGNORECASE,
)
# A value like this marks a return from a payment provider on an ordinary page
# (/vote/…?from=tribute_return): no hint there, and the marker is not carried
# over, where it would count the conversion twice.
_RETURN_VALUE_RE = re.compile(r'(?:^|[_-])return$', re.IGNORECASE)
# Client and server clocks differ; a decision made "in the future" by up to a
# day is still a decision.
_CLOCK_SKEW = 86400


def current_version(request) -> str | None:
    try:
        host = request.get_host()
    except Exception:
        return None
    return VERSION_BY_HOST.get(host)


def is_sensitive_route(request) -> bool:
    return bool(_SENSITIVE_PATH_RE.search(request.path))


def _is_sensitive_param(name, value) -> bool:
    return bool(_SENSITIVE_PARAM_RE.search(name) or _RETURN_VALUE_RE.search(value))


def _query_pairs(request):
    return parse_qsl(request.META.get('QUERY_STRING', ''), keep_blank_values=True)


def _safe_query(request) -> str:
    return urlencode([(name, value) for name, value in _query_pairs(request) if not _is_sensitive_param(name, value)])


def alternate_url(request, version: str) -> str:
    """The same page on ``version``'s host, or its home page for sensitive routes.

    The host comes only from VERSIONS; the request contributes a path, which
    always starts with '/', so the result cannot point anywhere else.
    """
    origin = 'https://' + VERSIONS[version]
    if is_sensitive_route(request):
        return origin + '/'
    query = _safe_query(request)
    return origin + escape_uri_path(request.path) + ('?' + query if query else '')


def saved_decision(request) -> tuple[str, str] | None:
    """('chosen' | 'stayed', version) from a still valid cookie, else None."""
    parts = (request.COOKIES.get(COOKIE_NAME) or '').split(':')
    if len(parts) != 3 or parts[1] not in VERSIONS or not parts[2].isdigit():
        return None
    kind, version, decided_at = parts[0], parts[1], int(parts[2])
    max_age = {'chosen': CHOSEN_MAX_AGE, 'stayed': STAYED_MAX_AGE}.get(kind)
    if max_age is None or not -_CLOCK_SKEW <= time.time() - decided_at < max_age:
        return None
    return kind, version


def suggested_version(request) -> str | None:
    """The other version to hint at on this page, if any."""
    current = current_version(request)
    if current is None or request.method != 'GET' or is_sensitive_route(request):
        return None
    if any(_is_sensitive_param(name, value) for name, value in _query_pairs(request)):
        return None
    if saved_decision(request) is not None:
        return None
    country = visitor_country(request)
    if country == UNKNOWN:
        return None
    wanted = RU if country == 'RU' else INTL
    return wanted if wanted != current else None


def site_version(request):
    """Template context for site/partials/site_version_*.html.

    Context processors run on every render_to_string(request=…), so a page
    built from many fragments would recompute it; keep it on the request.
    """
    cached = getattr(request, '_site_version_context', None)
    if cached is None:
        cached = request._site_version_context = _site_version_context(request)
    return cached


def _site_version_context(request):
    current = current_version(request)
    suggest = suggested_version(request)
    return {
        'site_version': {
            'current': current or '',
            'options': [
                {
                    'key': version,
                    'host': host,
                    'url': alternate_url(request, version),
                    'current': version == current,
                }
                for version, host in VERSIONS.items()
            ],
            'has_decision': saved_decision(request) is not None,
            'suggest': suggest or '',
            'suggest_url': alternate_url(request, suggest) if suggest else '',
        },
    }
