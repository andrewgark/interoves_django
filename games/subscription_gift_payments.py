"""One-off payment flows for Club subscription gifts."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings

from games.models import TributePurchase, SubscriptionGift, SubscriptionGiftPayment
from games.subscription_gifts import create_paid_gift
from games.telegram_linking import user_has_telegram_link
from games.tribute_config import club_gift_products_by_id, configured_club_gift
from games.club_yookassa_client import ClubPayment as Payment
from games.club_yookassa import club_yookassa_enabled
from games.yookassa_util import configure_yookassa_from_env

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GiftCheckoutResult:
    ok: bool
    message: str = ''
    payment_url: str = ''
    payment: SubscriptionGiftPayment | None = None
    http_status: int = 200


def start_tribute_gift(*, user, months: int) -> GiftCheckoutResult:
    product = configured_club_gift(months)
    if product is None:
        return GiftCheckoutResult(False, 'Этот срок подарочной подписки пока недоступен.', http_status=503)
    if not user_has_telegram_link(user):
        return GiftCheckoutResult(
            False,
            'Сначала привяжите Telegram, чтобы Tribute мог подтвердить оплату.',
            http_status=409,
        )
    telegram_user_id = user.profile.telegram_user_id
    with transaction.atomic():
        active = SubscriptionGiftPayment.objects.select_for_update().filter(
            purchaser=user,
            provider=SubscriptionGift.PROVIDER_TRIBUTE,
            telegram_user_id=telegram_user_id,
            duration_months=months,
            status=SubscriptionGiftPayment.STATUS_PENDING,
            created_at__gte=timezone.now() - timedelta(hours=2),
        ).select_related('gift').first()
        if active and active.gift.status == SubscriptionGift.STATUS_CREATED:
            return GiftCheckoutResult(True, payment_url=product.web_url, payment=active)
        gift, _code = create_paid_gift(
            purchaser=user,
            duration_months=months,
            provider=SubscriptionGift.PROVIDER_TRIBUTE,
            amount=product.amount,
            currency=product.currency,
        )
        payment = SubscriptionGiftPayment.objects.create(
            gift=gift,
            purchaser=user,
            provider=SubscriptionGift.PROVIDER_TRIBUTE,
            expected_amount=product.amount,
            currency=product.currency,
            duration_months=months,
            telegram_user_id=telegram_user_id,
            idempotency_key=uuid.uuid4().hex,
        )
    logger.info('subscription_gift_tribute_started payment_id=%s user_id=%s months=%s', payment.pk, user.pk, months)
    return GiftCheckoutResult(True, payment_url=product.web_url, payment=payment)


def yookassa_gift_amount(months: int) -> int:
    try:
        return int(getattr(settings, 'CLUB_GIFT_YOOKASSA_{}_AMOUNT_KOPECKS'.format(months)) or '')
    except (AttributeError, TypeError, ValueError):
        return 0


def start_yookassa_gift(*, user, months: int, return_url: str) -> GiftCheckoutResult:
    amount = yookassa_gift_amount(months)
    if not club_yookassa_enabled():
        return GiftCheckoutResult(False, 'Оплата в рублях сейчас недоступна.', http_status=503)
    if months not in (1, 3) or amount <= 0:
        return GiftCheckoutResult(False, 'Оплата подарка в рублях пока не настроена.', http_status=503)
    with transaction.atomic():
        active = SubscriptionGiftPayment.objects.select_for_update().filter(
            purchaser=user,
            provider=SubscriptionGift.PROVIDER_YOOKASSA,
            duration_months=months,
            status__in=(
                SubscriptionGiftPayment.STATUS_PENDING,
                SubscriptionGiftPayment.STATUS_MANUAL_REVIEW,
            ),
            created_at__gte=timezone.now() - timedelta(hours=2),
        ).select_related('gift').order_by('-created_at').first()
        if active and active.status == SubscriptionGiftPayment.STATUS_MANUAL_REVIEW:
            return GiftCheckoutResult(
                False,
                'Предыдущий платёж ещё проверяется. Повторно оплачивать подарок пока нельзя.',
                http_status=409,
                payment=active,
            )
        if active and active.confirmation_url:
            return GiftCheckoutResult(True, payment_url=active.confirmation_url, payment=active)
        gift, _code = create_paid_gift(
            purchaser=user,
            duration_months=months,
            provider=SubscriptionGift.PROVIDER_YOOKASSA,
            amount=amount,
            currency='RUB',
        )
        payment = SubscriptionGiftPayment.objects.create(
            gift=gift,
            purchaser=user,
            provider=SubscriptionGift.PROVIDER_YOOKASSA,
            expected_amount=amount,
            currency='RUB',
            duration_months=months,
            idempotency_key=uuid.uuid4().hex,
        )
    try:
        configure_yookassa_from_env()
        remote = Payment.create({
            'amount': {'value': '{:.2f}'.format(amount / 100), 'currency': 'RUB'},
            'capture': True,
            'description': 'Подарочная подписка Inter Oves на {} мес.'.format(months)[:128],
            'confirmation': {'type': 'redirect', 'return_url': return_url},
            'save_payment_method': False,
            'metadata': {
                'purpose': 'club_gift',
                'gift_payment_id': str(payment.pk),
                'gift_id': str(gift.pk),
                'user_id': str(user.pk),
                'months': str(months),
            },
        }, payment.idempotency_key)
    except Exception:
        logger.exception('subscription_gift_yookassa_create_failed payment_id=%s', payment.pk)
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.save(update_fields=['status', 'updated_at'])
        return GiftCheckoutResult(False, 'Платёж создаётся или проверяется. Обновите страницу через минуту.', http_status=502)
    confirmation_url = (remote.get('confirmation') or {}).get('confirmation_url') or ''
    payment.provider_payment_id = remote.get('id') or ''
    payment.confirmation_url = confirmation_url
    payment.save(update_fields=['provider_payment_id', 'confirmation_url', 'updated_at'])
    if not confirmation_url:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.save(update_fields=['status', 'updated_at'])
        return GiftCheckoutResult(False, 'Не получилось открыть оплату. Попробуйте позже.', http_status=502)
    return GiftCheckoutResult(True, payment_url=confirmation_url, payment=payment)


@transaction.atomic
def process_yookassa_gift_event(
    event_name: str, payment_data: dict, *, allow_manual_review: bool = False,
) -> bool:
    metadata = payment_data.get('metadata') or {}
    if str(metadata.get('purpose') or '') != 'club_gift':
        return False
    payment = SubscriptionGiftPayment.objects.select_for_update().filter(
        pk=metadata.get('gift_payment_id'), provider=SubscriptionGift.PROVIDER_YOOKASSA,
    ).select_related('gift').first()
    if payment is None:
        payment = SubscriptionGiftPayment.objects.select_for_update().filter(
            provider=SubscriptionGift.PROVIDER_YOOKASSA,
            provider_payment_id=payment_data.get('id') or '',
        ).select_related('gift').first()
    if payment is None:
        logger.warning('subscription_gift_yookassa_unmatched payment_id=%s', payment_data.get('id'))
        return True
    amount = payment_data.get('amount') or {}
    try:
        amount_minor = Decimal(str(amount.get('value'))) * 100
        valid_amount = (
            amount_minor == amount_minor.to_integral_value()
            and int(amount_minor) == payment.expected_amount
        )
    except (InvalidOperation, TypeError, ValueError):
        valid_amount = False
    if amount.get('currency') != payment.currency or not valid_amount:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.raw_event_excerpt = {'id': payment_data.get('id'), 'amount': amount, 'event': event_name}
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        return True
    payment.provider_payment_id = payment_data.get('id') or payment.provider_payment_id
    if event_name == 'payment.canceled':
        payment.status = SubscriptionGiftPayment.STATUS_CANCELED
        payment.save(update_fields=['status', 'provider_payment_id', 'updated_at'])
        from games.subscription_gifts import revoke_gift_access
        revoke_gift_access(payment.gift)
        return True
    # Provider webhooks are not guaranteed to arrive in order. A late success
    # must never resurrect a canceled/refunded payment. Reconciliation may
    # explicitly release a manually held payment after checking YooKassa.
    if payment.status != SubscriptionGiftPayment.STATUS_PENDING and not (
        allow_manual_review and payment.status == SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
    ):
        return True
    if payment.gift.status != SubscriptionGift.STATUS_CREATED:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.raw_event_excerpt = dict(
            payment.raw_event_excerpt or {},
            id=payment_data.get('id'), event=event_name,
            gift_status=payment.gift.status,
        )
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        logger.warning(
            'subscription_gift_yookassa_late_success payment_id=%s gift_status=%s',
            payment.pk, payment.gift.status,
        )
        return True
    now = timezone.now()
    payment.status = SubscriptionGiftPayment.STATUS_SUCCEEDED
    payment.succeeded_at = now
    payment.save(update_fields=['status', 'provider_payment_id', 'succeeded_at', 'updated_at'])
    payment.gift.status = SubscriptionGift.STATUS_PAID
    payment.gift.paid_at = now
    payment.gift.save(update_fields=['status', 'paid_at'])
    return True


def _payload_excerpt(data: dict) -> dict:
    return {
        key: data.get(key)
        for key in ('product_id', 'product_name', 'amount', 'currency', 'telegram_user_id', 'purchase_id', 'transaction_id')
    }


def _find_tribute_pending_payment(data: dict, product):
    candidates = list(SubscriptionGiftPayment.objects.select_for_update().filter(
        provider=SubscriptionGift.PROVIDER_TRIBUTE,
        telegram_user_id=data['telegram_user_id'],
        duration_months=product.months,
        status=SubscriptionGiftPayment.STATUS_PENDING,
    ).order_by('created_at')[:20])
    purchase_at = parse_datetime(data['purchase_created_at'])
    if purchase_at is None:
        return None
    if timezone.is_naive(purchase_at):
        purchase_at = timezone.make_aware(purchase_at, timezone.utc)
    candidates = [candidate for candidate in candidates if abs(
        (candidate.created_at - purchase_at).total_seconds()
    ) <= 24 * 60 * 60]
    candidates.sort(key=lambda candidate: abs(
        (candidate.created_at - purchase_at).total_seconds()
    ))
    # A timestamp is only a fallback correlation key. Never guess between
    # multiple pending gifts: a wrong assignment is harder to recover from
    # than a payment sent to manual review.
    if len(candidates) > 1:
        logger.warning(
            'subscription_gift_tribute_ambiguous purchase_id=%s product_id=%s',
            data['purchase_id'], data['product_id'],
        )
        return None
    return candidates[0] if candidates else None


@transaction.atomic
def process_tribute_gift_purchase(payload: dict) -> bool:
    """Apply a signed Tribute digital-product purchase, idempotently."""
    from games.tribute_service import normalize_purchase_payload

    data = normalize_purchase_payload(payload)
    product = club_gift_products_by_id().get(data['product_id'])
    if product is None:
        return False
    payment = SubscriptionGiftPayment.objects.select_for_update().filter(
        provider=SubscriptionGift.PROVIDER_TRIBUTE,
        purchase_id=data['purchase_id'],
    ).first()
    if payment is None:
        payment = _find_tribute_pending_payment(data, product)
    refund_recorded = TributePurchase.objects.filter(
        purchase_id=data['purchase_id'],
        status=TributePurchase.STATUS_REFUNDED,
    ).exists()
    if refund_recorded:
        if payment is not None and payment.status == SubscriptionGiftPayment.STATUS_PENDING:
            payment.status = SubscriptionGiftPayment.STATUS_CANCELED
            payment.raw_event_excerpt = dict(
                payment.raw_event_excerpt or {}, refund_before_purchase=True,
            )
            payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
            from games.subscription_gifts import revoke_gift_access
            revoke_gift_access(payment.gift)
        logger.warning('subscription_gift_tribute_purchase_already_refunded purchase_id=%s', data['purchase_id'])
        return True
    if data['amount'] != product.amount or data['currency'] != product.currency:
        logger.warning('subscription_gift_tribute_mismatch purchase_id=%s product_id=%s', data['purchase_id'], data['product_id'])
        if payment is not None and payment.status == SubscriptionGiftPayment.STATUS_PENDING:
            payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
            payment.raw_event_excerpt = _payload_excerpt(data)
            payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
            return True
        return False
    if payment is None:
        logger.warning('subscription_gift_tribute_unmatched purchase_id=%s product_id=%s', data['purchase_id'], data['product_id'])
        return False
    # Tribute may retry an old purchase event after a refund. Only a pending
    # payment can transition to succeeded; terminal states are monotonic.
    if payment.status != SubscriptionGiftPayment.STATUS_PENDING:
        return True
    if payment.gift.status != SubscriptionGift.STATUS_CREATED:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.raw_event_excerpt = dict(
            _payload_excerpt(data), gift_status=payment.gift.status,
        )
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        logger.warning(
            'subscription_gift_tribute_late_purchase payment_id=%s gift_status=%s',
            payment.pk, payment.gift.status,
        )
        return True
    if payment.expected_amount != data['amount'] or payment.currency != data['currency']:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.raw_event_excerpt = _payload_excerpt(data)
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        return False
    now = timezone.now()
    payment.status = SubscriptionGiftPayment.STATUS_SUCCEEDED
    payment.purchase_id = data['purchase_id']
    payment.provider_payment_id = data['transaction_id']
    payment.succeeded_at = now
    payment.raw_event_excerpt = _payload_excerpt(data)
    payment.save(update_fields=['status', 'purchase_id', 'provider_payment_id', 'succeeded_at', 'raw_event_excerpt', 'updated_at'])
    gift = SubscriptionGift.objects.select_for_update().get(pk=payment.gift_id)
    gift.status = SubscriptionGift.STATUS_PAID
    gift.paid_at = now
    gift.save(update_fields=['status', 'paid_at'])
    return True


@transaction.atomic
def process_tribute_gift_refund(payload: dict) -> bool:
    purchase_id = str(payload.get('purchase_id') or '').strip()
    if not purchase_id:
        return False
    payment = SubscriptionGiftPayment.objects.select_for_update().filter(
        provider=SubscriptionGift.PROVIDER_TRIBUTE, purchase_id=purchase_id,
    ).select_related('gift').first()
    if payment is None:
        return False
    payment.status = SubscriptionGiftPayment.STATUS_CANCELED
    payment.raw_event_excerpt = dict(payment.raw_event_excerpt or {}, refund_reason=payload.get('refund_reason', ''))
    payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
    from games.subscription_gifts import revoke_gift_access
    revoke_gift_access(payment.gift)
    return True


@transaction.atomic
def process_yookassa_gift_refund(payment_data: dict) -> bool:
    payment_id = str(payment_data.get('payment_id') or '').strip()
    if not payment_id:
        return False
    payment = SubscriptionGiftPayment.objects.select_for_update().filter(
        provider=SubscriptionGift.PROVIDER_YOOKASSA,
        provider_payment_id=payment_id,
    ).select_related('gift').first()
    if payment is None:
        return False
    refund_amount = payment_data.get('amount') or {}
    try:
        refund_minor = Decimal(str(refund_amount.get('value'))) * 100
        valid_refund = (
            refund_minor == refund_minor.to_integral_value()
            and int(refund_minor) == payment.expected_amount
            and refund_amount.get('currency') == payment.currency
        )
    except (InvalidOperation, TypeError, ValueError):
        valid_refund = False
    if not valid_refund:
        payment.status = SubscriptionGiftPayment.STATUS_MANUAL_REVIEW
        payment.raw_event_excerpt = {
            'payment_id': payment_id,
            'event': 'refund.succeeded',
            'refund_amount': refund_amount,
        }
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        logger.warning('subscription_gift_yookassa_refund_amount_mismatch payment_id=%s', payment_id)
        return False
    payment.status = SubscriptionGiftPayment.STATUS_CANCELED
    payment.raw_event_excerpt = {
        'payment_id': payment_id, 'event': 'refund.succeeded', 'refund_amount': refund_amount,
    }
    payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
    from games.subscription_gifts import revoke_gift_access
    revoke_gift_access(payment.gift)
    return True


@transaction.atomic
def manually_resolve_yookassa_gift_payment(payment_id: int, *, succeeded: bool) -> bool:
    payment = SubscriptionGiftPayment.objects.select_for_update().filter(
        pk=payment_id,
        provider=SubscriptionGift.PROVIDER_YOOKASSA,
        status=SubscriptionGiftPayment.STATUS_MANUAL_REVIEW,
    ).select_related('gift').first()
    if payment is None:
        return False
    now = timezone.now()
    if succeeded:
        payment.status = SubscriptionGiftPayment.STATUS_SUCCEEDED
        payment.succeeded_at = payment.succeeded_at or now
        payment.raw_event_excerpt = dict(payment.raw_event_excerpt or {}, manual_resolution='succeeded')
        payment.save(update_fields=['status', 'succeeded_at', 'raw_event_excerpt', 'updated_at'])
        payment.gift.status = SubscriptionGift.STATUS_PAID
        payment.gift.paid_at = payment.gift.paid_at or now
        payment.gift.save(update_fields=['status', 'paid_at'])
    else:
        payment.status = SubscriptionGiftPayment.STATUS_CANCELED
        payment.raw_event_excerpt = dict(payment.raw_event_excerpt or {}, manual_resolution='canceled')
        payment.save(update_fields=['status', 'raw_event_excerpt', 'updated_at'])
        from games.subscription_gifts import revoke_gift_access
        revoke_gift_access(payment.gift)
    return True


def reconcile_yookassa_gift_payments(*, limit: int = 50) -> int:
    """Reconcile gift payments whose YooKassa outcome was ambiguous."""
    payment_ids = list(
        SubscriptionGiftPayment.objects.filter(
            provider=SubscriptionGift.PROVIDER_YOOKASSA,
            status__in=(
                SubscriptionGiftPayment.STATUS_PENDING,
                SubscriptionGiftPayment.STATUS_MANUAL_REVIEW,
            ),
        ).exclude(provider_payment_id='').order_by('updated_at').values_list('pk', flat=True)[:limit]
    )
    changed = 0
    for payment_id in payment_ids:
        payment = SubscriptionGiftPayment.objects.filter(pk=payment_id).first()
        if payment is None or payment.status not in (
            SubscriptionGiftPayment.STATUS_PENDING,
            SubscriptionGiftPayment.STATUS_MANUAL_REVIEW,
        ):
            continue
        try:
            remote = dict(Payment.find_one(payment.provider_payment_id))
        except Exception:
            logger.exception(
                'subscription_gift_yookassa_reconcile_failed payment_id=%s',
                payment.pk,
            )
            continue
        status = str(remote.get('status') or '')
        if status == 'succeeded':
            if process_yookassa_gift_event('payment.succeeded', remote, allow_manual_review=True):
                payment.refresh_from_db(fields=('status',))
                changed += payment.status == SubscriptionGiftPayment.STATUS_SUCCEEDED
        elif status == 'canceled':
            if process_yookassa_gift_event('payment.canceled', remote, allow_manual_review=True):
                payment.refresh_from_db(fields=('status',))
                changed += payment.status == SubscriptionGiftPayment.STATUS_CANCELED
    return changed
