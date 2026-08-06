"""Tribute Shop API helpers. Credentials: env vars (prod) or secrets/*.txt under BASE_DIR (local)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

TRIBUTE_API_BASE = 'https://tribute.tg/api/v1'
TRIBUTE_USER_AGENT = 'Interoves/1.0 (+https://interoves.com; ticket-payments)'


def _secrets_dir() -> Path:
    return Path(settings.BASE_DIR) / 'secrets'


def _read_secret_file(name: str) -> str | None:
    try:
        return (_secrets_dir() / name).read_text(encoding='utf-8').strip() or None
    except OSError:
        return None


def get_tribute_api_key() -> str:
    key = os.environ.get('TRIBUTE_API_KEY') or _read_secret_file('tribute_api_key.txt')
    if not key:
        raise RuntimeError(
            'Missing Tribute API key: set TRIBUTE_API_KEY on the server '
            'or add secrets/tribute_api_key.txt under BASE_DIR.'
        )
    return key


def get_tribute_shop_id() -> int | None:
    raw = os.environ.get('TRIBUTE_SHOP_ID') or _read_secret_file('tribute_shop_id.txt')
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'Invalid TRIBUTE_SHOP_ID: {raw!r}') from exc


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


def create_shop_order(
    *,
    amount_rub: int,
    title: str,
    description: str,
    success_url: str,
    fail_url: str,
    customer_id: str | None = None,
    comment: str | None = None,
    shop_id: int | None = None,
    api_key: str | None = None,
    timeout_sec: float = 30.0,
) -> dict:
    """
    Create a Tribute shop order via POST /shop/orders.

    amount_rub is in rubles; Tribute expects kopecks for RUB.
    Returns the parsed order object (must include uuid / paymentUrl).
    """
    key = api_key if api_key is not None else get_tribute_api_key()
    resolved_shop_id = shop_id if shop_id is not None else get_tribute_shop_id()

    body: dict[str, Any] = {
        'amount': int(amount_rub) * 100,
        'currency': 'rub',
        'title': (title or '')[:100],
        'description': (description or '')[:300],
        'period': 'onetime',
        'successUrl': success_url,
        'failUrl': fail_url,
    }
    if resolved_shop_id is not None:
        body['shopId'] = resolved_shop_id
    if customer_id:
        body['customerId'] = str(customer_id)[:256]
    if comment:
        body['comment'] = comment

    request = Request(
        f'{TRIBUTE_API_BASE}/shop/orders',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Api-Key': key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': TRIBUTE_USER_AGENT,
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=timeout_sec) as resp:
            raw = resp.read().decode('utf-8')
            data = json.loads(raw) if raw else {}
    except HTTPError as exc:
        err_body = ''
        try:
            err_body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        logger.error(
            'Tribute create_shop_order HTTP %s customer_id=%s body=%s',
            exc.code,
            customer_id,
            err_body[:500],
        )
        raise RuntimeError(f'Tribute order create failed: HTTP {exc.code}') from exc
    except URLError as exc:
        logger.error('Tribute create_shop_order network error customer_id=%s: %s', customer_id, exc)
        raise RuntimeError('Tribute order create failed: network error') from exc

    order_uuid = data.get('uuid')
    if not order_uuid:
        raise RuntimeError('Tribute order create failed: missing uuid')
    if not data.get('paymentUrl') and not data.get('webappPaymentUrl'):
        raise RuntimeError('Tribute order create failed: missing payment URL')
    return data
