"""Low-overhead request correlation and broken-session diagnostics."""

from __future__ import annotations

import logging
import re
import socket
import time
import uuid
from importlib import import_module

from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
    load_backend,
)
from django.core import signing
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from games.auth_observability import (
    log_auth_event,
    log_authenticated_request,
    log_post_request,
    session_fingerprint,
)
from games.runtime import deployment_environment
from games.middleware.request_timing import timing_phase


logger = logging.getLogger('application')

_SEND_ATTEMPT_PATH = re.compile(r'^/send_attempt/(?P<task_id>\d+)/?$')


def _request_error_context(request) -> dict:
    """Return safe request metadata for an HTTP 500 forensic log."""
    resolver_match = getattr(request, 'resolver_match', None)
    path = getattr(request, 'path', '/') or '/'
    task_match = _SEND_ATTEMPT_PATH.match(path)
    elapsed_ms = getattr(request, '_interoves_elapsed_ms', None)
    if elapsed_ms is None:
        started_at = getattr(request, '_interoves_started_at', None)
        if started_at is not None:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return {
        'request_id': getattr(request, 'interoves_request_id', 'unavailable'),
        'method': getattr(request, 'method', 'unknown') or 'unknown',
        # Deliberately use path instead of get_full_path(): query parameters can
        # contain OAuth codes, tokens, or user-provided secrets.
        'path': path,
        'route': getattr(resolver_match, 'url_name', None) or 'unavailable',
        'task_id': task_match.group('task_id') if task_match else 'unavailable',
        'instance': (
            getattr(settings, 'INSTANCE_ID', '')
            or socket.gethostname()
            or 'unknown'
        ),
        'environment': deployment_environment(),
        'deploy_version': getattr(settings, 'SITE_DEPLOY_VERSION', '') or 'unavailable',
        'duration_ms': elapsed_ms,
    }


def _db_error_details(exception):
    """Extract bounded DB driver details from an exception chain."""
    seen = set()
    current = exception
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        args = getattr(current, 'args', ()) or ()
        module_name = current.__class__.__module__
        code = getattr(current, 'errno', None)
        is_db_error = code is not None or module_name.startswith(
            ('django.db', 'MySQLdb', 'psycopg')
        )
        if is_db_error:
            if code is None and args and isinstance(args[0], int):
                code = args[0]
            # Keep driver args useful but bounded; never include request
            # query/body/cookies in this record.
            safe_args = tuple(str(value)[:300] for value in args[:4])
            return code, repr(safe_args)[:800]
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    return None, None


def _log_http_500_exception(request, exception) -> None:
    context = _request_error_context(request)
    db_code, db_args = _db_error_details(exception)
    logger.exception(
        'http_500_uncaught request_id=%s method=%s path=%s route=%s '
        'task_id=%s environment=%s instance=%s deploy_version=%s duration_ms=%s '
        'exception_type=%s db_error_code=%s db_error_args=%s',
        context['request_id'],
        context['method'],
        context['path'],
        context['route'],
        context['task_id'],
        context['environment'],
        context['instance'],
        context['deploy_version'],
        _format_duration(context['duration_ms']),
        f'{exception.__class__.__module__}.{exception.__class__.__name__}',
        db_code if db_code is not None else 'unavailable',
        db_args or 'unavailable',
    )


def _log_http_500_response(request, status_code) -> None:
    context = _request_error_context(request)
    logger.error(
        'http_5xx_response status=%s request_id=%s method=%s path=%s route=%s '
        'task_id=%s environment=%s instance=%s deploy_version=%s duration_ms=%s',
        status_code,
        context['request_id'],
        context['method'],
        context['path'],
        context['route'],
        context['task_id'],
        context['environment'],
        context['instance'],
        context['deploy_version'],
        _format_duration(context['duration_ms']),
    )


def _format_duration(value):
    if value is None:
        return 'unavailable'
    return '{:.0f}'.format(value)


def _is_authenticated_audit_path(path: str) -> bool:
    return (
        path.startswith('/send_attempt/')
        or path.startswith('/send_hint_attempt/')
        or path.startswith('/send_raddle_assist/')
        or path.startswith('/send_raddle_ui/')
        or path.startswith('/alphabetty/') and path.endswith(('/guess/', '/hint/', '/suggest/'))
        or path.endswith('/timing/')
        or path.startswith('/accounts/')
        or path.startswith('/telegram/login/')
        or path.startswith('/telegram/callback/')
        or path == '/logout/'
        or path.endswith('/migrate-anon-attempts/')
    )


class RequestCorrelationMiddleware:
    """Give every Django response/log event an application request ID."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.interoves_request_id = uuid.uuid4().hex
        response = self.get_response(request)
        response['X-Request-ID'] = request.interoves_request_id
        return response


def _inspect_persisted_session(session_key):
    """Inspect an already anomalous cookie; never called for healthy sessions."""
    if not session_key:
        return 'session_cookie_missing', None
    if len(session_key) > 256:
        return 'session_decode_failed', None
    try:
        engine = import_module(settings.SESSION_ENGINE)
        store = engine.SessionStore(session_key=session_key)
        model = store.get_model_class()
    except (AttributeError, ImportError):
        return 'unknown', None

    try:
        row = model.objects.filter(session_key=session_key).only(
            'session_data', 'expire_date'
        ).first()
    except Exception:
        return 'unknown', None
    if row is None:
        return 'session_row_missing', None
    if row.expire_date <= timezone.now():
        return 'session_expired', None

    try:
        data = signing.loads(
            row.session_data,
            salt=store.key_salt,
            serializer=store.serializer,
        )
    except signing.BadSignature:
        return 'signature_mismatch', None
    except Exception:
        return 'session_decode_failed', None
    if not isinstance(data, dict):
        return 'session_decode_failed', None
    return None, data


def _classify_auth_payload(data):
    user_id = data.get(SESSION_KEY)
    if user_id is None:
        return None, None

    backend_path = data.get(BACKEND_SESSION_KEY)
    if not backend_path or backend_path not in settings.AUTHENTICATION_BACKENDS:
        return 'auth_backend_invalid', user_id
    try:
        load_backend(backend_path)
    except Exception:
        return 'auth_backend_invalid', user_id

    try:
        user = get_user_model()._default_manager.get(pk=user_id)
    except get_user_model().DoesNotExist:
        return 'user_missing', user_id
    except Exception:
        return 'user_missing', user_id

    if not getattr(user, 'is_active', True):
        return 'user_inactive', user_id

    if hasattr(user, 'get_session_auth_hash'):
        stored_hash = data.get(HASH_SESSION_KEY, '')
        current_hash = user.get_session_auth_hash()
        matches = constant_time_compare(stored_hash, current_hash)
        if not matches and hasattr(user, 'get_session_auth_fallback_hash'):
            matches = any(
                constant_time_compare(stored_hash, fallback_hash)
                for fallback_hash in user.get_session_auth_fallback_hash()
            )
        if not matches:
            return 'auth_hash_mismatch', user_id

    return 'unknown', user_id


class AuthSessionDiagnosticMiddleware:
    """Log only requests carrying a broken or rejected Django session cookie."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        if not raw_session_key:
            # A missing cookie is normal for anonymous traffic. Without a separate
            # client marker there is no safe way to call it a logout, so do not log.
            return self.get_response(request)

        with timing_phase(request, 'auth_session_load'):
            try:
                pre_auth_data = dict(request.session.items())
            except Exception:
                pre_auth_data = None

        classification = None
        user_id = None
        auth_payload = None

        with timing_phase(request, 'auth_session_recovery'):
            if pre_auth_data is None:
                classification, restored_data = _inspect_persisted_session(raw_session_key)
                if restored_data and SESSION_KEY in restored_data:
                    auth_payload = restored_data
                    classification = None
            elif SESSION_KEY in pre_auth_data:
                auth_payload = pre_auth_data
            elif not pre_auth_data:
                classification, restored_data = _inspect_persisted_session(raw_session_key)
                if restored_data and SESSION_KEY in restored_data:
                    auth_payload = restored_data
                    classification = None
                elif restored_data is not None:
                    # A valid anonymous session is not an auth anomaly.
                    classification = None

        try:
            response = self.get_response(request)
        except Exception:
            # A malformed decoded payload can itself make Django auth raise. Emit
            # the already-safe diagnosis, then preserve the original exception.
            if classification:
                log_auth_event(
                    'auth_session_anomaly',
                    request,
                    classification=classification,
                    user_id=None,
                    session_fingerprint=session_fingerprint(raw_session_key),
                )
            raise

        with timing_phase(request, 'auth_session_post'):
            if auth_payload is not None:
                try:
                    is_authenticated = bool(request.user.is_authenticated)
                except Exception:
                    is_authenticated = False
                if is_authenticated:
                    return response
                classification, user_id = _classify_auth_payload(auth_payload)

            if classification:
                log_auth_event(
                    'auth_session_anomaly',
                    request,
                    classification=classification,
                    user_id=str(user_id)[:64] if user_id is not None else None,
                    session_fingerprint=session_fingerprint(raw_session_key),
                )
        return response


class AuthenticatedRequestAuditMiddleware:
    """Correlate selected authenticated requests without logging payloads."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception as exc:
            _log_http_500_exception(request, exc)
            if getattr(request, 'method', '') == 'POST':
                log_post_request(request, error=True)
            from games.telegram.notify import notify_admin_site_error

            notify_admin_site_error(request, exception=exc)
            raise
        if getattr(response, 'status_code', 0) >= 500:
            _log_http_500_response(request, response.status_code)
            from games.telegram.notify import notify_admin_site_error

            notify_admin_site_error(request, status_code=response.status_code)
        with timing_phase(request, 'auth_audit_response'):
            if getattr(request, 'method', '') == 'POST':
                user = getattr(request, 'user', None)
                if (
                    getattr(user, 'is_authenticated', False)
                    and _is_authenticated_audit_path(getattr(request, 'path', '') or '/')
                ):
                    # Preserve the established event name for the gameplay/auth
                    # allowlist while enriching it with the same forensic context.
                    log_authenticated_request(request, response)
                else:
                    log_post_request(request, response)
            elif _is_authenticated_audit_path(getattr(request, 'path', '') or '/'):
                log_authenticated_request(request, response)
        return response
