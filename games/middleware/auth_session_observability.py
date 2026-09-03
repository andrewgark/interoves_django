"""Low-overhead request correlation and broken-session diagnostics."""

from __future__ import annotations

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

from games.auth_observability import log_auth_event, session_fingerprint


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

        try:
            pre_auth_data = dict(request.session.items())
        except Exception:
            pre_auth_data = None

        classification = None
        user_id = None
        auth_payload = None

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
