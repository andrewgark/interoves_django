"""Shared donation confirm/reject helpers (NOWPayments IPN)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from games.models import Donation

logger = logging.getLogger(__name__)

DONATION_ORDER_PREFIX = 'donation-'
MIN_DONATION_RUB = 50
RECENT_DONATIONS_LIMIT = 10
SESSION_DONATION_IDS_KEY = 'donate_recent_ids'
STALE_PENDING_DONATION_HOURS = 8


@dataclass(frozen=True)
class DonationConfirmResult:
    changed: bool
    already_confirmed: bool


@dataclass(frozen=True)
class DonationRejectResult:
    changed: bool
    already_final: bool


def donation_order_id(donation_id: int) -> str:
    return '{}{}'.format(DONATION_ORDER_PREFIX, donation_id)


def parse_donation_order_id(order_id) -> int | None:
    if order_id is None:
        return None
    text = str(order_id)
    if not text.startswith(DONATION_ORDER_PREFIX):
        return None
    raw = text[len(DONATION_ORDER_PREFIX) :]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def extract_pay_fields(event_json: dict) -> tuple[str, str]:
    """Return (pay_amount, pay_currency) strings from a NOWPayments IPN payload."""
    amount = event_json.get('pay_amount')
    if amount is None or amount == '':
        amount = event_json.get('actually_paid')
    if amount is None or amount == '':
        amount = event_json.get('price_amount')

    currency = event_json.get('pay_currency') or ''
    if not currency:
        currency = event_json.get('price_currency') or ''

    amount_str = ''
    if amount is not None and amount != '':
        try:
            amount_str = format(Decimal(str(amount)), 'f')
        except (InvalidOperation, ValueError, TypeError):
            amount_str = str(amount)[:64]

    return amount_str[:64], str(currency).lower()[:16]


def confirm_donation(
    donation: Donation,
    *,
    pay_amount: str | None = None,
    pay_currency: str | None = None,
    nowpayments_id: str | None = None,
    source: str = 'unknown',
) -> DonationConfirmResult:
    """Idempotently mark a donation Confirmed and store paid amount/currency."""
    already_confirmed = donation.status == 'Confirmed'
    update_fields = []

    if nowpayments_id and not donation.nowpayments_id:
        donation.nowpayments_id = nowpayments_id
        update_fields.append('nowpayments_id')

    if pay_amount and not donation.pay_amount:
        donation.pay_amount = str(pay_amount)[:64]
        update_fields.append('pay_amount')
    if pay_currency and not donation.pay_currency:
        donation.pay_currency = str(pay_currency).lower()[:16]
        update_fields.append('pay_currency')

    if already_confirmed:
        if update_fields:
            donation.save(update_fields=update_fields)
        return DonationConfirmResult(changed=False, already_confirmed=True)

    donation.status = 'Confirmed'
    update_fields.append('status')
    if not donation.confirmed_at:
        donation.confirmed_at = timezone.now()
        update_fields.append('confirmed_at')
    donation.save(update_fields=update_fields)
    logger.info(
        'confirm_donation: donation_id=%s amount_rub=%s pay=%s %s source=%s',
        donation.pk,
        donation.amount_rub,
        donation.pay_amount,
        donation.pay_currency,
        source,
    )
    return DonationConfirmResult(changed=True, already_confirmed=False)


def reject_donation(donation: Donation, *, source: str = 'unknown') -> DonationRejectResult:
    """Reject a pending donation; no-op if already Confirmed/Rejected."""
    if donation.status != 'Pending':
        return DonationRejectResult(changed=False, already_final=True)

    donation.status = 'Rejected'
    donation.save(update_fields=['status'])
    logger.info('reject_donation: donation_id=%s source=%s', donation.pk, source)
    return DonationRejectResult(changed=True, already_final=False)


def remember_donation_in_session(request, donation_id: int) -> None:
    """Track donation ids in the browser session so anonymous users see a recent list."""
    ids = list(request.session.get(SESSION_DONATION_IDS_KEY) or [])
    donation_id = int(donation_id)
    ids = [donation_id] + [i for i in ids if int(i) != donation_id]
    request.session[SESSION_DONATION_IDS_KEY] = ids[: max(RECENT_DONATIONS_LIMIT * 2, 20)]
    request.session.modified = True


def recent_donations_for_request(request, limit: int | None = None):
    """Donations visible to this visitor: own (if logged in) and/or session-tracked."""
    from django.db.models import Q

    n = RECENT_DONATIONS_LIMIT if limit is None else limit
    session_ids = []
    for raw_id in request.session.get(SESSION_DONATION_IDS_KEY) or []:
        try:
            session_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    if request.user.is_authenticated:
        q = Q(user=request.user)
        if session_ids:
            q |= Q(id__in=session_ids)
        return list(Donation.objects.filter(q).order_by('-created_at')[:n])

    if not session_ids:
        return []
    return list(Donation.objects.filter(id__in=session_ids).order_by('-created_at')[:n])


def stale_pending_donations(*, hours: int | None = None):
    """Pending donations older than the threshold (default 8h)."""
    from datetime import timedelta

    threshold = hours if hours is not None else STALE_PENDING_DONATION_HOURS
    cutoff = timezone.now() - timedelta(hours=threshold)
    return Donation.objects.filter(status='Pending', created_at__lt=cutoff).order_by('created_at')


def reject_stale_pending_donations(*, hours: int | None = None) -> int:
    """
    Auto-reject Pending donations older than the threshold.

    Returns the number of donations newly rejected.
    """
    rejected = 0
    for donation in stale_pending_donations(hours=hours).iterator():
        result = reject_donation(donation, source='stale_pending')
        if result.changed:
            rejected += 1
    if rejected:
        logger.info('reject_stale_pending_donations: rejected=%s hours=%s', rejected, hours or STALE_PENDING_DONATION_HOURS)
    return rejected
