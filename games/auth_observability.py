"""Safe, structured logging for authentication lifecycle events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone


logger = logging.getLogger('interoves.auth')
_SESSION_FINGERPRINT_HEX_LENGTH = 20
_KEY_FINGERPRINT_HEX_LENGTH = 16
_KEY_FINGERPRINT_MESSAGE = b'interoves-auth-key-fingerprint-v1'


def _key_fingerprint(value: str) -> str:
    if not value:
        return 'unavailable'
    return hmac.new(
        value.encode('utf-8'),
        _KEY_FINGERPRINT_MESSAGE,
        hashlib.sha256,
    ).hexdigest()[:_KEY_FINGERPRINT_HEX_LENGTH]


def session_fingerprint(session_key: str | None) -> str:
    """Return a non-reversible, cross-instance session correlation value."""
    logging_key = getattr(settings, 'AUTH_LOG_FINGERPRINT_KEY', '')
    if not logging_key or not session_key:
        return 'unavailable'
    return hmac.new(
        logging_key.encode('utf-8'),
        session_key.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()[:_SESSION_FINGERPRINT_HEX_LENGTH]


def _instance_identifier() -> str:
    return (getattr(settings, 'INSTANCE_ID', '') or socket.gethostname() or 'unknown')


def _request_fields(request) -> dict:
    if request is None:
        return {
            'host': 'unavailable',
            'path': 'unavailable',
            'method': 'unavailable',
            'request_id': 'unavailable',
        }
    try:
        host = request.get_host()
    except Exception:
        host = 'invalid'
    return {
        'host': host,
        # Deliberately exclude the query string: OAuth codes must not enter logs.
        'path': getattr(request, 'path', '') or '/',
        'method': getattr(request, 'method', '') or 'unknown',
        'request_id': getattr(request, 'interoves_request_id', 'unavailable'),
    }


def _safe_user_id(value):
    if value is None:
        return None
    return str(value)[:64]


def log_auth_event(event: str, request=None, **fields) -> None:
    payload = {
        'event': event,
        'timestamp': timezone.now().isoformat(),
        'instance': _instance_identifier(),
        'deploy_version': getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unavailable',
        **_request_fields(request),
        **fields,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    logger.info(json.dumps(payload, sort_keys=True, separators=(',', ':')))


def _request_session_key(request) -> str | None:
    if request is None:
        return None
    session = getattr(request, 'session', None)
    session_key = getattr(session, 'session_key', None)
    if session_key:
        return session_key
    return getattr(request, 'COOKIES', {}).get(settings.SESSION_COOKIE_NAME)


def _request_session_expiry(request) -> str:
    try:
        return request.session.get_expiry_date().isoformat()
    except Exception:
        return 'unavailable'


def _logout_reason(request) -> str:
    path = getattr(request, 'path', '') if request is not None else ''
    if '/password/reset/key/' in path:
        return 'password_reset'
    return 'explicit_logout'


@receiver(user_logged_in, dispatch_uid='interoves.auth_observability.login')
def log_user_login(sender, request, user, **kwargs):
    log_auth_event(
        'login',
        request,
        user_id=_safe_user_id(getattr(user, 'pk', None)),
        session_fingerprint=session_fingerprint(_request_session_key(request)),
        session_expires_at=_request_session_expiry(request),
    )


@receiver(user_logged_out, dispatch_uid='interoves.auth_observability.logout')
def log_user_logout(sender, request, user, **kwargs):
    log_auth_event(
        'logout',
        request,
        user_id=_safe_user_id(getattr(user, 'pk', None)),
        session_fingerprint=session_fingerprint(_request_session_key(request)),
        logout_reason=_logout_reason(request),
    )


def log_startup_auth_configuration() -> None:
    logging_key = getattr(settings, 'AUTH_LOG_FINGERPRINT_KEY', '')
    log_auth_event(
        'auth_startup',
        secret_key_fingerprint=_key_fingerprint(settings.SECRET_KEY),
        session_fingerprint_key_configured=bool(logging_key),
        session_fingerprint_key_fingerprint=_key_fingerprint(logging_key),
        session_cookie_age_seconds=settings.SESSION_COOKIE_AGE,
        session_save_every_request=settings.SESSION_SAVE_EVERY_REQUEST,
        session_cookie_secure=settings.SESSION_COOKIE_SECURE,
        csrf_cookie_secure=settings.CSRF_COOKIE_SECURE,
    )
