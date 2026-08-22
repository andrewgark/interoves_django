"""Validated configuration for Tribute Digital Product browser payments."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings


SUPPORTED_CURRENCIES = frozenset({'EUR', 'RUB'})
SUPPORTED_MERCHANTS = frozenset({'ru_self_employed', 'am_ie'})
# INTERNAL: existing legacy Tribute rows use legacy_unspecified, so seller review
# is required before this route can be enabled. Never expose this marker in UI.
TRIBUTE_LEGAL_REVIEW = 'existing_tribute_merchant_is_not_proven_by_repository_configuration'


@dataclass(frozen=True)
class TributeProduct:
    kind: str
    product_id: int
    web_url: str
    amount: int
    currency: str

    @property
    def amount_major(self) -> Decimal:
        return Decimal(self.amount) / Decimal('100')

    @property
    def amount_display(self) -> str:
        value = self.amount_major
        if value == value.to_integral():
            return '{:,.0f}'.format(value).replace(',', ' ')
        return '{:,.2f}'.format(value).replace(',', ' ')


def _read_product(kind: str) -> tuple[TributeProduct | None, list[str]]:
    prefix = 'TRIBUTE_{}_PRODUCT_'.format(kind.upper())
    raw_id = str(getattr(settings, prefix + 'ID', '') or '').strip()
    web_url = str(getattr(settings, prefix + 'WEB_URL', '') or '').strip()
    raw_amount = str(getattr(settings, prefix + 'AMOUNT', '') or '').strip()
    currency = str(getattr(settings, prefix + 'CURRENCY', '') or '').strip().upper()
    errors = []

    try:
        product_id = int(raw_id)
        if product_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append('{}ID must be a positive integer'.format(prefix))
        product_id = 0

    try:
        amount = int(raw_amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append('{}AMOUNT must be a positive integer in cents/kopecks'.format(prefix))
        amount = 0

    if currency not in SUPPORTED_CURRENCIES:
        errors.append('{}CURRENCY must be EUR or RUB'.format(prefix))

    parsed = urlparse(web_url)
    if (
        parsed.scheme != 'https'
        or parsed.hostname != 'web.tribute.tg'
        or not parsed.path.startswith('/p/')
        or parsed.path == '/p/'
        or parsed.query
        or parsed.fragment
    ):
        errors.append('{}WEB_URL must be an https://web.tribute.tg/p/... URL'.format(prefix))

    if errors:
        return None, errors
    return TributeProduct(kind, product_id, web_url, amount, currency), []


def product_configuration() -> tuple[dict[str, TributeProduct], list[str]]:
    products = {}
    errors = []
    for kind in ('regular', 'discount'):
        product, product_errors = _read_product(kind)
        errors.extend(product_errors)
        if product is not None:
            products[kind] = product
    if len(products) == 2 and products['regular'].product_id == products['discount'].product_id:
        errors.append('Tribute regular and discount product IDs must be different')
        products = {}
    return products, errors


def products_by_id() -> dict[int, TributeProduct]:
    products, _errors = product_configuration()
    return {product.product_id: product for product in products.values()}


def configuration_errors() -> list[str]:
    _products, errors = product_configuration()
    if not str(getattr(settings, 'TRIBUTE_API_KEY', '') or '').strip():
        errors.append('TRIBUTE_API_KEY is required')
    merchant = str(getattr(settings, 'TRIBUTE_MERCHANT', '') or '').strip()
    if merchant not in SUPPORTED_MERCHANTS:
        errors.append('TRIBUTE_MERCHANT must be ru_self_employed or am_ie after legal review')
    if not getattr(settings, 'TRIBUTE_LEGAL_REVIEW_APPROVED', False):
        errors.append('TRIBUTE_LEGAL_REVIEW_APPROVED must be enabled after seller review')
    if not str(getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or '').strip():
        errors.append('TELEGRAM_BOT_USERNAME is required for account linking')
    if not str(getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip():
        errors.append('TELEGRAM_BOT_TOKEN is required for account linking')
    return errors


def tribute_checkout_enabled() -> bool:
    return bool(getattr(settings, 'TRIBUTE_ENABLED', False)) and not configuration_errors()


def configured_product(kind: str) -> TributeProduct | None:
    products, _errors = product_configuration()
    return products.get(kind)


def merchant() -> str:
    value = str(getattr(settings, 'TRIBUTE_MERCHANT', '') or '').strip()
    return value if value in SUPPORTED_MERCHANTS else 'legacy_unspecified'


def merchant_public_copy() -> tuple[str, str]:
    if merchant() == 'ru_self_employed':
        return 'Продавец: Андрей Гаркавый, плательщик НПД, РФ', '/sellers/#russia'
    if merchant() == 'am_ie':
        return 'Продавец: Andrei Garkavyi IE, Republic of Armenia', '/sellers/#armenia'
    return 'Оплата через Tribute', '/sellers/'
