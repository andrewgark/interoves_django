"""Tribute Digital Product webhook signature helpers."""
from __future__ import annotations

import hashlib
import hmac
from django.conf import settings


def get_tribute_api_key() -> str:
    key = str(getattr(settings, 'TRIBUTE_API_KEY', '') or '').strip()
    if not key:
        raise RuntimeError('Missing Tribute API key: set TRIBUTE_API_KEY on the server.')
    return key


def tribute_webhook_url() -> str:
    """Stable public webhook URL (prefer SITE_BASE_URL behind proxies)."""
    base = (getattr(settings, 'SITE_BASE_URL', None) or 'https://interoves.com').rstrip('/')
    return f'{base}/tribute/webhook/'


def compute_webhook_signature(raw_body: bytes, api_key: str) -> str:
    return hmac.new(api_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    raw_body: bytes,
    trbt_signature: str | None,
    *,
    api_key: str | None = None,
) -> bool:
    if not trbt_signature:
        return False
    key = api_key if api_key is not None else get_tribute_api_key()
    expected = compute_webhook_signature(raw_body, key)
    return hmac.compare_digest(expected, trbt_signature)
