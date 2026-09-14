"""Canonical anonymous analytics identity: server-issued cookie + HMAC signature.

The browser UUID cookie is the only client-visible actor identifier. POST,
header, query, and localStorage values are never authority for gameplay or
analytics attribution. A separate HttpOnly signature cookie proves the UUID
was issued or upgraded by this server.

Phase E requires a valid HMAC signature. A well-formed unsigned ``interoves_anon``
cookie is no longer adopted: the server issues a fresh identity instead.
Header-only spoofing is rejected because those fields are ignored.
"""
from __future__ import annotations

import binascii
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver

logger = logging.getLogger('interoves.analytics_identity')

ANON_COOKIE_NAME = 'interoves_anon'
ANON_SIG_COOKIE_NAME = 'interoves_anon_sig'
# Match the existing JS cookie (about 366 days). Do not shorten retention.
ANON_COOKIE_MAX_AGE = 31622400
SIGNING_CONTEXT = b'interoves-anon-identity-v1'
_REQUEST_STATE_ATTR = '_interoves_anon_identity'
_MIN_KEY_LEN = 1
_MAX_KEY_LEN = 64
_KEY_EXTRA_CHARS = frozenset('-_.~')


@dataclass
class AnonymousIdentity:
    key: str
    signature: str
    issue_key_cookie: bool = False
    issue_sig_cookie: bool = False


def should_skip_anonymous_identity(request) -> bool:
    path = getattr(request, 'path', '') or ''
    return path.startswith((
        '/static/',
        '/media/',
        '/health',
        '/favicon.ico',
        '/robots.txt',
        '/meta/',
    ))


def is_valid_anon_key(value) -> bool:
    value = (value or '').strip()
    if not (_MIN_KEY_LEN <= len(value) <= _MAX_KEY_LEN):
        return False
    return all(char.isalnum() or char in _KEY_EXTRA_CHARS for char in value)


def new_anonymous_key() -> str:
    return str(uuid.uuid4())


def _signing_key_bytes() -> bytes:
    raw = getattr(settings, 'ANALYTICS_ANON_SIGNING_KEY', None) or settings.SECRET_KEY
    if isinstance(raw, bytes):
        return raw
    return str(raw).encode('utf-8')


def sign_anon_key(key: str) -> str:
    digest = hmac.new(
        _signing_key_bytes(),
        SIGNING_CONTEXT + b'\n' + key.encode('ascii'),
        hashlib.sha256,
    ).digest()
    return binascii.hexlify(digest).decode('ascii')


def signature_is_valid(key: str, signature: str) -> bool:
    if not key or not signature:
        return False
    expected = sign_anon_key(key)
    provided = str(signature).strip()
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(expected, provided)


def anon_key_fingerprint(key: str) -> str:
    """Irreversible diagnostic id; never log the raw cookie or signature."""
    if not key:
        return 'unavailable'
    digest = hmac.new(
        _signing_key_bytes(),
        b'interoves-anon-fingerprint-v1\n' + key.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return digest[:16]


def _cookie_kwargs(*, httponly: bool) -> dict:
    return {
        'max_age': ANON_COOKIE_MAX_AGE,
        'path': '/',
        'samesite': 'Lax',
        'secure': bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)),
        'httponly': httponly,
    }


def _read_cookie(request, name: str) -> str:
    try:
        cookies = getattr(request, 'COOKIES', None) or {}
        return str(cookies.get(name) or '').strip()
    except Exception:
        return ''


def issue_fresh_identity() -> AnonymousIdentity:
    key = new_anonymous_key()
    return AnonymousIdentity(
        key=key,
        signature=sign_anon_key(key),
        issue_key_cookie=True,
        issue_sig_cookie=True,
    )


def resolve_anonymous_identity(request) -> AnonymousIdentity:
    """Build the canonical browser identity from cookies only.

    Never raises for malformed credentials: issues a fresh identity instead.
    """
    try:
        presented = _read_cookie(request, ANON_COOKIE_NAME)
        signature = _read_cookie(request, ANON_SIG_COOKIE_NAME)
        if is_valid_anon_key(presented):
            if signature_is_valid(presented, signature):
                return AnonymousIdentity(
                    key=presented,
                    signature=signature,
                )
        return issue_fresh_identity()
    except Exception:
        logger.exception('anonymous identity resolve failed; issuing a fresh key')
        return issue_fresh_identity()


def bind_anonymous_identity(request) -> AnonymousIdentity | None:
    existing = getattr(request, _REQUEST_STATE_ATTR, None)
    if existing is not None:
        return existing
    if should_skip_anonymous_identity(request):
        return None
    identity = resolve_anonymous_identity(request)
    setattr(request, _REQUEST_STATE_ATTR, identity)
    return identity


def browser_anon_key(request) -> str | None:
    """Canonical browser anonymous UUID, including during an authenticated session."""
    identity = bind_anonymous_identity(request)
    if identity is None:
        return None
    return identity.key


def gameplay_anon_key(request) -> str | None:
    """Anonymous actor for unauthenticated gameplay/analytics writes."""
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return None
    return browser_anon_key(request)


def rotate_anonymous_identity(request) -> AnonymousIdentity:
    identity = issue_fresh_identity()
    setattr(request, _REQUEST_STATE_ATTR, identity)
    return identity


def apply_anonymous_identity_cookies(request, response) -> None:
    identity = getattr(request, _REQUEST_STATE_ATTR, None)
    if identity is None:
        return
    if should_skip_anonymous_identity(request):
        return
    try:
        if identity.issue_key_cookie:
            response.set_cookie(
                ANON_COOKIE_NAME,
                identity.key,
                **_cookie_kwargs(httponly=False),
            )
        if identity.issue_sig_cookie:
            response.set_cookie(
                ANON_SIG_COOKIE_NAME,
                identity.signature,
                **_cookie_kwargs(httponly=True),
            )
    except Exception:
        logger.exception('anonymous identity cookie write failed')


def stamp_anon_identity(target, key: str | None = None) -> str:
    """Put a signed identity on a Django test Client or a request ``COOKIES`` map."""
    if not key:
        key = new_anonymous_key()
    signature = sign_anon_key(key)
    jar = getattr(target, 'cookies', None)
    mapping = getattr(target, 'COOKIES', None)
    if jar is not None:
        jar[ANON_COOKIE_NAME] = key
        jar[ANON_SIG_COOKIE_NAME] = signature
    if mapping is not None:
        mapping[ANON_COOKIE_NAME] = key
        mapping[ANON_SIG_COOKIE_NAME] = signature
    if jar is None and mapping is None:
        raise TypeError('stamp_anon_identity needs a test Client or request COOKIES map')
    return key


def attach_anon_cookie(client, key: str | None = None) -> str:
    """Test helper: present a signed server-issued anonymous identity."""
    return stamp_anon_identity(client, key)


def attach_unsigned_anon_cookie(client, key: str | None = None) -> str:
    """Test helper: present only ``interoves_anon`` (Phase E must not adopt it)."""
    if not key:
        key = new_anonymous_key()
    client.cookies[ANON_COOKIE_NAME] = key
    try:
        del client.cookies[ANON_SIG_COOKIE_NAME]
    except KeyError:
        pass
    return key


@receiver(user_logged_out, dispatch_uid='interoves.anon_identity.logout_rotate')
def rotate_anonymous_identity_on_logout(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        rotate_anonymous_identity(request)
    except Exception:
        logger.exception('anonymous identity logout rotation failed')
