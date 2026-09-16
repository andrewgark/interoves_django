"""Safe, structured logging for authentication lifecycle events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import socket

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone


logger = logging.getLogger('interoves.auth')
_SESSION_FINGERPRINT_HEX_LENGTH = 20
_KEY_FINGERPRINT_HEX_LENGTH = 16
_KEY_FINGERPRINT_MESSAGE = b'interoves-auth-key-fingerprint-v1'
_AUTHENTICATION_METHODS_SESSION_KEY = 'account_authentication_methods'


def normalized_user_agent(user_agent: str | None) -> dict:
    """Return a small, non-raw client description suitable for auth logs.

    User-Agent is an assertion made by the HTTP client, not device attestation.
    Keep this parser deliberately conservative and avoid persisting the raw
    header or a long-lived raw-UA hash.
    """
    value = (user_agent or '').strip()
    browser_family = 'Other'
    browser_major = None
    if re.search(r'Edg(?:A|iOS)?/', value):
        browser_family = 'Edge'
        match = re.search(r'Edg(?:A|iOS)?/(\d+)', value)
    elif re.search(r'(?:YaBrowser)/', value):
        browser_family = 'Yandex'
        match = re.search(r'YaBrowser/(\d+)', value)
    elif re.search(r'(?:CriOS|Chrome)/', value):
        browser_family = 'Chrome'
        match = re.search(r'(?:CriOS|Chrome)/(\d+)', value)
    elif re.search(r'(?:FxiOS|Firefox)/', value):
        browser_family = 'Firefox'
        match = re.search(r'(?:FxiOS|Firefox)/(\d+)', value)
    elif re.search(r'(?:OPiOS|OPR)/', value):
        browser_family = 'Opera'
        match = re.search(r'(?:OPiOS|OPR)/(\d+)', value)
    elif re.search(r'Safari/', value):
        browser_family = 'Safari'
        match = re.search(r'Version/(\d+)', value)
    else:
        match = None
    browser_major = match.group(1) if match else None

    if re.search(r'iPad', value):
        os_family, device_family = 'iPadOS', 'iPad'
    elif re.search(r'iPhone|iPod', value):
        os_family, device_family = 'iOS', 'iPhone'
    elif re.search(r'Android', value):
        os_family = 'Android'
        device_family = 'Android'
        if re.search(r'Mobile', value):
            device_family = 'Android phone'
        elif re.search(r'Tablet', value):
            device_family = 'Android tablet'
    elif re.search(r'Windows NT', value):
        os_family, device_family = 'Windows', 'Desktop'
    elif re.search(r'Macintosh|Mac OS X', value):
        os_family, device_family = 'macOS', 'Desktop'
    elif re.search(r'Linux', value):
        os_family, device_family = 'Linux', 'Desktop'
    else:
        os_family, device_family = 'Other', 'Other'

    return {
        'browser_family': browser_family,
        'browser_major': browser_major,
        'os_family': os_family,
        'device_family': device_family,
    }


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


def _request_user_agent(request) -> dict:
    if request is None:
        return normalized_user_agent(None)
    return normalized_user_agent(request.META.get('HTTP_USER_AGENT'))


def _safe_user_id(value):
    if value is None:
        return None
    return str(value)[:64]


def log_auth_event(event: str, request=None, **fields) -> None:
    payload = {
        'event': event,
        'event_name': event,
        'timestamp': timezone.now().isoformat(),
        'instance': _instance_identifier(),
        'deploy_version': getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unavailable',
        'user_agent': _request_user_agent(request),
        **_request_fields(request),
        **fields,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        logger.info(json.dumps(payload, sort_keys=True, separators=(',', ':')))
    except Exception:
        # Security telemetry must never turn a successful gameplay request into
        # an application error (for example, during a broken stderr handler).
        return


def _request_session_key(request) -> str | None:
    if request is None:
        return None
    session = getattr(request, 'session', None)
    session_key = getattr(session, 'session_key', None)
    if session_key:
        return session_key
    return getattr(request, 'COOKIES', {}).get(settings.SESSION_COOKIE_NAME)


def request_session_fingerprint(request) -> str:
    return session_fingerprint(_request_session_key(request))


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


def _auth_method_fields(request) -> dict:
    """Extract only provider/method names from allauth's session marker."""
    methods = []
    try:
        methods = request.session.get(_AUTHENTICATION_METHODS_SESSION_KEY, [])
    except Exception:
        pass
    if methods and isinstance(methods[-1], dict):
        latest = methods[-1]
        fields = {'auth_method': latest.get('method')}
        if latest.get('provider'):
            fields['provider'] = latest['provider']
        return fields

    path = getattr(request, 'path', '') or ''
    if path.startswith('/accounts/') and '/login' in path:
        provider = path.split('/')[2] if len(path.split('/')) > 2 else None
        if provider and provider not in {'login', 'signup'}:
            return {'auth_method': 'socialaccount', 'provider': provider}
        return {'auth_method': 'password'}
    if path.startswith('/telegram/'):
        return {'auth_method': 'socialaccount', 'provider': 'telegram'}
    return {}


@receiver(user_logged_in, dispatch_uid='interoves.auth_observability.login')
def log_user_login(sender, request, user, **kwargs):
    log_auth_event(
        'login',
        request,
        user_id=_safe_user_id(getattr(user, 'pk', None)),
        session_fingerprint=session_fingerprint(_request_session_key(request)),
        session_expires_at=_request_session_expiry(request),
        session_rotation_reason='login',
        event_name='auth_login_success',
        **_auth_method_fields(request),
    )


@receiver(user_logged_out, dispatch_uid='interoves.auth_observability.logout')
def log_user_logout(sender, request, user, **kwargs):
    log_auth_event(
        'logout',
        request,
        user_id=_safe_user_id(getattr(user, 'pk', None)),
        session_fingerprint=session_fingerprint(_request_session_key(request)),
        logout_reason=_logout_reason(request),
        event_name='auth_logout',
    )


@receiver(user_login_failed, dispatch_uid='interoves.auth_observability.login_failed')
def log_user_login_failed(sender, request, credentials, **kwargs):
    # Deliberately ignore credentials: even usernames/emails can be sensitive in
    # some auth flows, and passwords/tokens must never reach structured logs.
    log_auth_event(
        'auth_login_failure',
        request,
        success=False,
        failure_class='authentication_failed',
        **_auth_method_fields(request),
    )


def log_authenticated_request(request, response) -> None:
    """Log a narrow allowlist of authenticated requests for forensics."""
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return
    log_auth_event(
        'authenticated_gameplay_request' if getattr(
            request, 'interoves_gameplay_request', False
        ) else 'authenticated_request',
        request,
        user_id=_safe_user_id(getattr(user, 'pk', None)),
        session_fingerprint=session_fingerprint(_request_session_key(request)),
        status=getattr(response, 'status_code', None),
        actor_kind=getattr(request, 'interoves_gameplay_actor_kind', None),
        task_id=getattr(request, 'interoves_gameplay_task_id', None),
        route_name=getattr(request, 'resolver_match', None).url_name
        if getattr(request, 'resolver_match', None) else None,
        result=getattr(request, 'interoves_gameplay_context_result', None),
        attempt_id=getattr(request, 'interoves_attempt_id', None),
    )


def log_gameplay_attempt_created(request, *, attempt, actor_kind=None) -> None:
    """Emit a minimal request-to-row link without logging attempt content."""
    anon_fingerprint = None
    if actor_kind == 'anon':
        from games.gameplay_context import anonymous_actor_fingerprint
        anon_fingerprint = anonymous_actor_fingerprint(getattr(attempt, 'anon_key', None))
    log_auth_event(
        'gameplay_attempt_created',
        request,
        attempt_id=getattr(attempt, 'pk', None),
        user_id=_safe_user_id(getattr(getattr(attempt, 'user', None), 'pk', None)),
        actor_kind=actor_kind,
        anon_fingerprint=anon_fingerprint,
        task_id=getattr(getattr(attempt, 'task', None), 'pk', None),
        session_fingerprint=session_fingerprint(_request_session_key(request))
        if getattr(getattr(request, 'user', None), 'is_authenticated', False)
        else None,
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
