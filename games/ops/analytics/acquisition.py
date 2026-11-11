"""Where a player came from: the first touch of a browser, kept with the player.

Metrika knows traffic sources but not backend players, and its visitor ids
reset (October 2026), so retention per source cannot be measured there. When
the server first identifies a browser (a fresh ``interoves_anon`` key), it
notes that request's UTM tags, external referrer host, landing path and site
host in a signed cookie. When that browser's player starts their first game,
the touch is copied onto ``PlayerAnalyticsState`` (``first_*`` fields).

Only new browsers get the cookie, and only actors without earlier starts get
the fields, so players who predate this code are never relabelled as direct
traffic. Bots that never start a game leave no rows.
"""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing

logger = logging.getLogger('interoves.analytics_acquisition')

FIRST_TOUCH_COOKIE_NAME = 'interoves_ft'
FIRST_TOUCH_COOKIE_MAX_AGE = 31622400  # same as interoves_anon
_SIGNING_SALT = 'interoves.first_touch.v1'
_REQUEST_ATTR = '_interoves_first_touch'

# Model field -> (cookie key, max length).
FIRST_TOUCH_FIELDS = {
    'first_utm_source': ('us', 64),
    'first_utm_medium': ('um', 64),
    'first_utm_campaign': ('uc', 128),
    'first_referrer_host': ('rh', 128),
    'first_landing_path': ('lp', 255),
    'first_site_host': ('sh', 64),
}
_UTM_PARAMS = {'us': 'utm_source', 'um': 'utm_medium', 'uc': 'utm_campaign'}

_current_touch: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    'interoves_first_touch', default=None,
)


def _clean(value, limit):
    return str(value or '').strip()[:limit]


def _host(value):
    try:
        return (urlsplit(value).hostname or '').lower()
    except ValueError:
        return ''


def _is_own_host(host):
    if not host:
        return False
    own = {h.lower().lstrip('.') for h in getattr(settings, 'ALLOWED_HOSTS', []) if h and h != '*'}
    own.update({'interoves.com', 'localhost', '127.0.0.1'})
    return any(host == name or host.endswith('.' + name) for name in own)


def first_touch_from_request(request) -> dict:
    """The acquisition facts of this request, in the compact cookie shape."""
    params = getattr(request, 'GET', {}) or {}
    limits = dict(FIRST_TOUCH_FIELDS.values())
    touch = {key: _clean(params.get(name), limits[key]) for key, name in _UTM_PARAMS.items()}
    referrer = _host((getattr(request, 'META', {}) or {}).get('HTTP_REFERER', ''))
    touch['rh'] = '' if _is_own_host(referrer) else referrer[:128]
    touch['lp'] = _clean(getattr(request, 'path', ''), 255)
    try:
        touch['sh'] = _clean(request.get_host().split(':')[0].lower(), 64)
    except Exception:
        touch['sh'] = ''
    touch['at'] = int(datetime.now(dt_timezone.utc).timestamp())
    return {key: value for key, value in touch.items() if value}


def _read_cookie(request) -> dict | None:
    raw = (getattr(request, 'COOKIES', None) or {}).get(FIRST_TOUCH_COOKIE_NAME)
    if not raw:
        return None
    try:
        value = signing.loads(raw, salt=_SIGNING_SALT)
    except signing.BadSignature:
        return None
    return value if isinstance(value, dict) else None


def bind_first_touch(request, *, new_browser: bool):
    """Resolve this request's first touch and expose it to analytics writes.

    Returns the contextvar token for :func:`release_first_touch`; the cookie
    for a new browser is written by :func:`apply_first_touch_cookie`.
    """
    touch = None
    try:
        touch = _read_cookie(request)
        if touch is None and new_browser:
            touch = first_touch_from_request(request)
            setattr(request, _REQUEST_ATTR, touch)
    except Exception:
        logger.exception('first touch resolve failed')
    return _current_touch.set(touch)


def release_first_touch(token) -> None:
    _current_touch.reset(token)


def apply_first_touch_cookie(request, response) -> None:
    """Persist a first touch resolved on this request (new browsers only)."""
    touch = getattr(request, _REQUEST_ATTR, None)
    if not touch:
        return
    try:
        response.set_cookie(
            FIRST_TOUCH_COOKIE_NAME,
            signing.dumps(touch, salt=_SIGNING_SALT, compress=True),
            max_age=FIRST_TOUCH_COOKIE_MAX_AGE,
            path='/',
            samesite='Lax',
            secure=bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)),
            httponly=True,
        )
    except Exception:
        logger.exception('first touch cookie write failed')


def current_first_touch() -> dict | None:
    return _current_touch.get()


def first_touch_field_values(touch: dict) -> dict:
    """Model field values for a cookie-shaped touch (empty strings when absent)."""
    values = {
        field: _clean(touch.get(key), limit)
        for field, (key, limit) in FIRST_TOUCH_FIELDS.items()
    }
    try:
        values['first_touch_at'] = datetime.fromtimestamp(int(touch['at']), tz=dt_timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError):
        values['first_touch_at'] = datetime.now(dt_timezone.utc)
    return values


def record_first_touch(actor: dict, *, started_game) -> None:
    """Copy the request's first touch onto a brand-new player's analytics state.

    ``actor`` is the exclusive identity dict used for analytics rows;
    ``started_game`` is the ``PlayerStartedGame`` just created for it. Teams
    and players with any earlier start are skipped. Never raises.
    """
    touch = current_first_touch()
    if not touch or actor.get('team') is not None:
        return
    from django.db import transaction

    from games.models import PlayerAnalyticsState, PlayerStartedGame
    from games.ops.analytics.persistence import create_or_reread_analytics_row

    try:
        # A savepoint: a failure here must not poison the gameplay transaction.
        with transaction.atomic():
            if PlayerStartedGame.objects.filter(**actor).exclude(pk=started_game.pk).exists():
                return
            state, _ = create_or_reread_analytics_row(PlayerAnalyticsState, lookup=actor)
            if state.first_touch_at is not None:
                return
            PlayerAnalyticsState.objects.filter(pk=state.pk, first_touch_at__isnull=True).update(
                **first_touch_field_values(touch),
            )
    except Exception:
        logger.exception('first touch record failed')
