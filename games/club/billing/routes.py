"""Ticket checkout routes and server-side pricing.

The selected route is the source of truth for provider, merchant, currency and
legal copy. Amounts are always recalculated from the team on the server.
"""
from dataclasses import dataclass


RUSSIAN_CARD = 'russian_card'
INTERNATIONAL_CARD = 'international_card'
TRIBUTE_CARD = 'tribute_card'
CRYPTO = 'crypto'


@dataclass(frozen=True)
class TicketPaymentRoute:
    key: str
    provider: str
    merchant: str
    currency: str
    terms_url: str
    seller_anchor: str
    enabled: bool


ROUTES = {
    RUSSIAN_CARD: TicketPaymentRoute(
        key=RUSSIAN_CARD,
        provider='yookassa',
        merchant='ru_self_employed',
        currency='RUB',
        terms_url='/terms/russia/',
        seller_anchor='/sellers/#russia',
        enabled=True,
    ),
    INTERNATIONAL_CARD: TicketPaymentRoute(
        key=INTERNATIONAL_CARD,
        provider='vpos',
        merchant='am_ie',
        currency='AMD',
        terms_url='/terms/armenia/',
        seller_anchor='/sellers/#armenia',
        enabled=False,
    ),
    TRIBUTE_CARD: TicketPaymentRoute(
        key=TRIBUTE_CARD,
        provider='tribute_digital',
        merchant='legacy_unspecified',
        currency='EUR',
        terms_url='/terms/tribute/',
        seller_anchor='/sellers/',
        enabled=False,
    ),
    CRYPTO: TicketPaymentRoute(
        key=CRYPTO,
        provider='nowpayments',
        merchant='ru_self_employed',
        currency='RUB',
        terms_url='/terms/crypto/',
        seller_anchor='/sellers/#russia',
        enabled=True,
    ),
}


def route_for(key):
    try:
        route = ROUTES[key]
    except KeyError as exc:
        raise ValueError('Unknown ticket payment route: {}'.format(key)) from exc
    if key == TRIBUTE_CARD:
        from dataclasses import replace
        from games.tribute_config import configured_product, merchant, tribute_checkout_enabled

        product = configured_product('regular')
        route = replace(
            route,
            merchant=merchant(),
            currency=product.currency if product else route.currency,
            enabled=tribute_checkout_enabled(),
        )
    return route


def unit_price_for(team, route_key):
    """Return the independent unit price for a route without converting currencies."""
    if route_key == TRIBUTE_CARD:
        from games.tribute_config import configured_product

        kind = 'discount' if team is not None and int(getattr(team, 'ticket_price', 2000)) == 500 else 'regular'
        product = configured_product(kind)
        return product.amount_major if product else 0
    if route_key == INTERNATIONAL_CARD:
        raw = getattr(team, 'ticket_price_amd', 10000) if team is not None else 10000
        fallback = 10000
    else:
        raw = getattr(team, 'ticket_price', 2000) if team is not None else 2000
        fallback = 2000
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def amount_for(team, route_key, tickets):
    if route_key == TRIBUTE_CARD and int(tickets) != 1:
        raise ValueError('Tribute Digital Product v1 supports exactly one ticket')
    return unit_price_for(team, route_key) * int(tickets)
