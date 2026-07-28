"""NOWPayments API helpers. Credentials: env vars (prod) or secrets/*.txt under BASE_DIR (local)."""
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

NOWPAYMENTS_API_BASE = 'https://api.nowpayments.io/v1'
NOWPAYMENTS_EMBED_BASE = 'https://nowpayments.io/embeds/payment-widget'


def _secrets_dir() -> Path:
    return Path(settings.BASE_DIR) / 'secrets'


def _read_secret_file(name: str) -> str | None:
    try:
        return (_secrets_dir() / name).read_text(encoding='utf-8').strip() or None
    except OSError:
        return None


def get_nowpayments_api_key() -> str:
    key = os.environ.get('NOWPAYMENTS_API_KEY') or _read_secret_file('nowpayments_api_key.txt')
    if not key:
        raise RuntimeError(
            'Missing NOWPayments API key: set NOWPAYMENTS_API_KEY on the server '
            'or add secrets/nowpayments_api_key.txt under BASE_DIR.'
        )
    return key


def get_nowpayments_ipn_secret() -> str:
    secret = os.environ.get('NOWPAYMENTS_IPN_SECRET') or _read_secret_file('nowpayments_ipn_secret.txt')
    if not secret:
        raise RuntimeError(
            'Missing NOWPayments IPN secret: set NOWPAYMENTS_IPN_SECRET on the server '
            'or add secrets/nowpayments_ipn_secret.txt under BASE_DIR.'
        )
    return secret


def embed_url_for_invoice(invoice_id: str | int) -> str:
    return f'{NOWPAYMENTS_EMBED_BASE}?iid={invoice_id}'


def _sort_object(value: Any) -> Any:
    """Recursively sort dict keys for IPN HMAC (NOWPayments docs)."""
    if isinstance(value, dict):
        return {k: _sort_object(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_sort_object(item) for item in value]
    return value


def compute_ipn_signature(payload: dict, ipn_secret: str) -> str:
    sorted_payload = _sort_object(payload)
    serialized = json.dumps(sorted_payload, separators=(',', ':'), ensure_ascii=False)
    return hmac.new(
        ipn_secret.encode('utf-8'),
        serialized.encode('utf-8'),
        hashlib.sha512,
    ).hexdigest()


def verify_ipn_signature(payload: dict, x_nowpayments_sig: str | None, *, ipn_secret: str | None = None) -> bool:
    if not x_nowpayments_sig:
        return False
    secret = ipn_secret if ipn_secret is not None else get_nowpayments_ipn_secret()
    expected = compute_ipn_signature(payload, secret)
    return hmac.compare_digest(expected, x_nowpayments_sig)


def create_invoice(
    *,
    price_amount: float | int,
    price_currency: str = 'rub',
    order_id: str,
    order_description: str,
    ipn_callback_url: str,
    success_url: str | None = None,
    cancel_url: str | None = None,
    api_key: str | None = None,
    timeout_sec: float = 30.0,
) -> dict:
    """
    Create a NOWPayments invoice via POST /v1/invoice.

    Returns the parsed JSON body (must include id / invoice_url).
    """
    key = api_key if api_key is not None else get_nowpayments_api_key()
    body: dict[str, Any] = {
        'price_amount': price_amount,
        'price_currency': price_currency,
        'order_id': str(order_id),
        'order_description': order_description[:400],
        'ipn_callback_url': ipn_callback_url,
    }
    if success_url:
        body['success_url'] = success_url
    if cancel_url:
        body['cancel_url'] = cancel_url

    request = Request(
        f'{NOWPAYMENTS_API_BASE}/invoice',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'x-api-key': key,
            'Content-Type': 'application/json',
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
            'NOWPayments create_invoice HTTP %s order_id=%s body=%s',
            exc.code,
            order_id,
            err_body[:500],
        )
        raise RuntimeError(f'NOWPayments invoice create failed: HTTP {exc.code}') from exc
    except URLError as exc:
        logger.error('NOWPayments create_invoice network error order_id=%s: %s', order_id, exc)
        raise RuntimeError('NOWPayments invoice create failed: network error') from exc

    invoice_id = data.get('id') or data.get('invoice_id')
    if not invoice_id:
        raise RuntimeError('NOWPayments invoice create failed: missing invoice id')
    return data
