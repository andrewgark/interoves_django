"""YooKassa billing for Inter Oves Club (RUB monthly + annual).

Does not touch TicketRequest / team-ticket payments. Entitlement remains
ClubSubscription.paid_until via games.club_access.has_club_access.
"""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone
from games.club_yookassa_client import ClubPayment as Payment

from games.models import ClubSubscription, ClubYooKassaPayment, SavedPaymentMethod
from games.yookassa_util import configure_yookassa_from_env

logger = logging.getLogger(__name__)

CURRENCY = 'RUB'
AMOUNT_INTRO_KOPECKS = 700_00
AMOUNT_MONTHLY_KOPECKS = 900_00
AMOUNT_ANNUAL_KOPECKS = 9000_00

PENDING_REUSE_MINUTES = 45
METADATA_PURPOSE = 'club_subscription'


@contextmanager
def _billing_lock(user_id):
    """Shared lock order: user -> subscription -> payment/method.

    Held through recurring API submission, including SDK retries. Detach cannot
    complete while a worker can still send a request with the old credential.
    SQLite development databases need a write lock (no SELECT FOR UPDATE).
    """
    with transaction.atomic():
        users = get_user_model().objects
        if connection.vendor == 'sqlite':
            users.filter(pk=user_id).update(last_login=F('last_login'))
        users.select_for_update().get(pk=user_id)
        yield


def _active_method(subscription):
    return SavedPaymentMethod.objects.filter(
        pk=subscription.saved_payment_method_id, user_id=subscription.user_id,
        provider='yookassa', method_type='bank_card', is_active=True,
        detached_at__isnull=True, provider_payment_method_id__isnull=False,
    ).exclude(provider_payment_method_id='').first()


def detach_yookassa_payment_method(user) -> StartPaymentResult:
    with _billing_lock(user.pk):
        subscription = ClubSubscription.objects.select_for_update().filter(user=user).first()
        now = timezone.now()
        methods = SavedPaymentMethod.objects.filter(user=user, provider='yookassa')
        method_ids = list(methods.filter(is_active=True).values_list('pk', flat=True))
        # Clear ALL credentials, including inactive leftovers; never retain a token
        # for audit. Historical rows contain internal ids and timestamps only.
        methods.update(provider_payment_method_id=None, is_active=False, card_last4='',
                       detached_at=now, updated_at=now)
        pending = False
        if subscription:
            subscription.saved_payment_method = None
            subscription.payment_method_detached_at = now
            if subscription.provider == ClubSubscription.PROVIDER_YOOKASSA:
                subscription.auto_renew = False
                subscription.next_charge_at = None
                subscription.cancelled_at = subscription.cancelled_at or now
                if subscription.plan == ClubSubscription.PLAN_MONTHLY:
                    subscription.status = ClubSubscription.STATUS_CANCELLED
                pending = subscription.yookassa_payments.filter(
                    kind=ClubYooKassaPayment.KIND_RECURRING_MONTHLY,
                    status=ClubYooKassaPayment.STATUS_PENDING, submitted_at__isnull=False,
                ).exists()
                transaction.on_commit(lambda: logger.info(
                    'subscription_auto_renew_disabled user_id=%s subscription_id=%s reason=detach',
                    user.pk, subscription.pk,
                ))
            subscription.save()
        transaction.on_commit(lambda: logger.info(
            'payment_method_detached user_id=%s saved_payment_method_ids=%s payment_in_flight=%s',
            user.pk, method_ids, pending,
        ))
    message = 'Карта отвязана. Автопродление отключено.'
    if pending:
        message += (' Платёж на продление был запущен до отвязки; '
                    'он ещё может завершиться. Новых автоматических списаний не будет.')
    return StartPaymentResult(True, message=message)


def club_yookassa_enabled() -> bool:
    return bool(getattr(settings, 'CLUB_YOOKASSA_ENABLED', False))


def yookassa_recurring_enabled() -> bool:
    return bool(getattr(settings, 'YOOKASSA_RECURRING_ENABLED', False))


def intro_available(subscription: ClubSubscription | None) -> bool:
    return subscription is None or subscription.intro_offer_used_at is None


def initial_monthly_amount_kopecks(subscription: ClubSubscription | None) -> int:
    return AMOUNT_INTRO_KOPECKS if intro_available(subscription) else AMOUNT_MONTHLY_KOPECKS


def kopecks_to_major_str(kopecks: int) -> str:
    return '{:.2f}'.format(Decimal(kopecks) / Decimal(100))


def add_calendar_months(dt: datetime, months: int) -> datetime:
    return dt + relativedelta(months=months)


def add_calendar_years(dt: datetime, years: int) -> datetime:
    return dt + relativedelta(years=years)


def period_key_for(kind: str, period_start: datetime) -> str:
    return '{}:{}'.format(period_start.date().isoformat(), kind)


@dataclass(frozen=True)
class StartPaymentResult:
    ok: bool
    reason: str = ''
    message: str = ''
    payment: ClubYooKassaPayment | None = None
    confirmation_url: str = ''
    http_status: int = 200


def _get_or_create_subscription(user) -> ClubSubscription:
    subscription, _ = ClubSubscription.objects.get_or_create(
        user=user,
        defaults={
            'provider': ClubSubscription.PROVIDER_YOOKASSA,
            'status': ClubSubscription.STATUS_EXPIRED,
        },
    )
    return subscription


def user_has_blocking_club_access(user, *, now=None) -> bool:
    """True when the user already has paid-through Club access (any provider)."""
    now = now or timezone.now()
    subscription = ClubSubscription.objects.filter(user=user).first()
    if subscription is None:
        return False
    return subscription.grants_access(now)


def _reuse_pending_payment(subscription: ClubSubscription, kind: str) -> ClubYooKassaPayment | None:
    cutoff = timezone.now() - timedelta(minutes=PENDING_REUSE_MINUTES)
    if subscription.payment_method_detached_at:
        cutoff = max(cutoff, subscription.payment_method_detached_at)
    return (
        ClubYooKassaPayment.objects.filter(
            club_subscription=subscription,
            kind=kind,
            status=ClubYooKassaPayment.STATUS_PENDING,
            created_at__gte=cutoff,
        )
        .exclude(confirmation_url='')
        .order_by('-created_at')
        .first()
    )


def _create_yookassa_payment(
    *,
    local_payment: ClubYooKassaPayment,
    amount_kopecks: int,
    description: str,
    save_payment_method: bool,
    return_url: str,
    payment_method_id: str | None = None,
) -> dict:
    configure_yookassa_from_env()
    payload = {
        'amount': {
            'value': kopecks_to_major_str(amount_kopecks),
            'currency': CURRENCY,
        },
        'capture': True,
        'description': description[:128],
        'metadata': {
            'purpose': METADATA_PURPOSE,
            'subscription_id': str(local_payment.club_subscription_id),
            'subscription_payment_id': str(local_payment.pk),
            'user_id': str(local_payment.user_id),
            'plan': local_payment.club_subscription.plan or '',
            'kind': local_payment.kind,
        },
    }
    if payment_method_id:
        payload['payment_method_id'] = payment_method_id
    else:
        payload['confirmation'] = {
            'type': 'redirect',
            'return_url': return_url,
        }
        payload['save_payment_method'] = bool(save_payment_method)
        if save_payment_method:
            # Only bank cards are in the first recurring approval scope.
            payload['payment_method_data'] = {'type': 'bank_card'}

    payment = Payment.create(payload, local_payment.idempotency_key)
    return dict(payment)


def start_monthly_subscription(user, *, return_url: str) -> StartPaymentResult:
    if not club_yookassa_enabled():
        return StartPaymentResult(
            False, 'disabled', 'Оплата российской картой пока недоступна.', http_status=503,
        )
    with _billing_lock(user.pk):
        subscription = ClubSubscription.objects.select_for_update().filter(user=user).first()
        if subscription is None:
            subscription = ClubSubscription.objects.create(
                user=user,
                provider=ClubSubscription.PROVIDER_YOOKASSA,
                status=ClubSubscription.STATUS_EXPIRED,
            )
            subscription = ClubSubscription.objects.select_for_update().get(pk=subscription.pk)
        rejoining_after_detach = bool(
            subscription.provider == ClubSubscription.PROVIDER_YOOKASSA
            and subscription.plan == ClubSubscription.PLAN_MONTHLY
            and subscription.payment_method_detached_at
            and not subscription.auto_renew and not _active_method(subscription)
        )
        if subscription.grants_access() and not rejoining_after_detach:
            return StartPaymentResult(
                False,
                'already_subscribed',
                'Подписка уже активна. Дождитесь конца оплаченного периода.',
                http_status=409,
            )

        reused = _reuse_pending_payment(subscription, ClubYooKassaPayment.KIND_INITIAL_MONTHLY)
        if reused is not None:
            return StartPaymentResult(
                True, payment=reused, confirmation_url=reused.confirmation_url,
            )

        amount = initial_monthly_amount_kopecks(subscription)
        now = timezone.now()
        period_start = max(now, subscription.paid_until or now)
        period_end = add_calendar_months(period_start, 1)
        idempotency_key = uuid.uuid4().hex
        period_key = '{}:{}'.format(ClubYooKassaPayment.KIND_INITIAL_MONTHLY, idempotency_key)
        try:
            local = ClubYooKassaPayment.objects.create(
                club_subscription=subscription,
                user=user,
                kind=ClubYooKassaPayment.KIND_INITIAL_MONTHLY,
                period_start=period_start,
                period_end=period_end,
                period_key=period_key,
                amount=amount,
                currency=CURRENCY,
                status=ClubYooKassaPayment.STATUS_PENDING,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            return StartPaymentResult(
                False, 'conflict', 'Платёж уже создаётся. Обновите страницу.', http_status=409,
            )

        subscription.provider = ClubSubscription.PROVIDER_YOOKASSA
        subscription.plan = ClubSubscription.PLAN_MONTHLY
        subscription.status = ClubSubscription.STATUS_PENDING
        subscription.save(update_fields=['provider', 'plan', 'status', 'updated_at'])

    description = (
        'Подписка Inter Oves — первый месяц'
        if amount == AMOUNT_INTRO_KOPECKS
        else 'Подписка Inter Oves — 1 месяц'
    )
    try:
        payment_data = _create_yookassa_payment(
            local_payment=local,
            amount_kopecks=amount,
            description=description,
            save_payment_method=True,
            return_url=return_url,
        )
    except Exception:
        logger.error(
            'subscription_initial_payment_created_failed user_id=%s payment_id=%s',
            user.pk, local.pk,
        )
        local.failure_code = 'create_failed'
        local.status = ClubYooKassaPayment.STATUS_CANCELED
        local.save(update_fields=['failure_code', 'status', 'updated_at'])
        return StartPaymentResult(
            False, 'yookassa', 'Не получилось создать платёж. Попробуйте позже.', http_status=502,
        )

    confirmation_url = (payment_data.get('confirmation') or {}).get('confirmation_url') or ''
    local.yookassa_payment_id = payment_data.get('id') or ''
    local.confirmation_url = confirmation_url
    local.save(update_fields=['yookassa_payment_id', 'confirmation_url', 'updated_at'])
    logger.info(
        'subscription_initial_payment_created user_id=%s payment_pk=%s amount=%s',
        user.pk, local.pk, amount,
    )
    if not confirmation_url:
        return StartPaymentResult(
            False, 'yookassa', 'Не получилось открыть оплату. Попробуйте позже.', http_status=502,
        )
    return StartPaymentResult(True, payment=local, confirmation_url=confirmation_url)


def start_annual_subscription(user, *, return_url: str) -> StartPaymentResult:
    if not club_yookassa_enabled():
        return StartPaymentResult(
            False, 'disabled', 'Оплата российской картой пока недоступна.', http_status=503,
        )
    with _billing_lock(user.pk):
        subscription = ClubSubscription.objects.select_for_update().filter(user=user).first()
        if subscription is None:
            subscription = ClubSubscription.objects.create(
                user=user,
                provider=ClubSubscription.PROVIDER_YOOKASSA,
                status=ClubSubscription.STATUS_EXPIRED,
            )
            subscription = ClubSubscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.grants_access():
            return StartPaymentResult(
                False,
                'already_subscribed',
                'Подписка уже активна. Дождитесь конца оплаченного периода.',
                http_status=409,
            )

        reused = _reuse_pending_payment(subscription, ClubYooKassaPayment.KIND_ANNUAL)
        if reused is not None:
            return StartPaymentResult(
                True, payment=reused, confirmation_url=reused.confirmation_url,
            )

        now = timezone.now()
        period_end = add_calendar_years(now, 1)
        idempotency_key = uuid.uuid4().hex
        period_key = '{}:{}'.format(ClubYooKassaPayment.KIND_ANNUAL, idempotency_key)
        try:
            local = ClubYooKassaPayment.objects.create(
                club_subscription=subscription,
                user=user,
                kind=ClubYooKassaPayment.KIND_ANNUAL,
                period_start=now,
                period_end=period_end,
                period_key=period_key,
                amount=AMOUNT_ANNUAL_KOPECKS,
                currency=CURRENCY,
                status=ClubYooKassaPayment.STATUS_PENDING,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            return StartPaymentResult(
                False, 'conflict', 'Платёж уже создаётся. Обновите страницу.', http_status=409,
            )

        subscription.provider = ClubSubscription.PROVIDER_YOOKASSA
        subscription.plan = ClubSubscription.PLAN_ANNUAL
        subscription.status = ClubSubscription.STATUS_PENDING
        subscription.save(update_fields=['provider', 'plan', 'status', 'updated_at'])

    try:
        payment_data = _create_yookassa_payment(
            local_payment=local,
            amount_kopecks=AMOUNT_ANNUAL_KOPECKS,
            description='Доступ к Inter Oves — 12 месяцев',
            save_payment_method=False,
            return_url=return_url,
        )
    except Exception:
        logger.error(
            'subscription_annual_payment_created_failed user_id=%s payment_id=%s',
            user.pk, local.pk,
        )
        local.failure_code = 'create_failed'
        local.status = ClubYooKassaPayment.STATUS_CANCELED
        local.save(update_fields=['failure_code', 'status', 'updated_at'])
        return StartPaymentResult(
            False, 'yookassa', 'Не получилось создать платёж. Попробуйте позже.', http_status=502,
        )

    confirmation_url = (payment_data.get('confirmation') or {}).get('confirmation_url') or ''
    local.yookassa_payment_id = payment_data.get('id') or ''
    local.confirmation_url = confirmation_url
    local.save(update_fields=['yookassa_payment_id', 'confirmation_url', 'updated_at'])
    logger.info(
        'subscription_initial_payment_created user_id=%s payment_pk=%s kind=annual',
        user.pk, local.pk,
    )
    if not confirmation_url:
        return StartPaymentResult(
            False, 'yookassa', 'Не получилось открыть оплату. Попробуйте позже.', http_status=502,
        )
    return StartPaymentResult(True, payment=local, confirmation_url=confirmation_url)


def cancel_yookassa_subscription(user) -> StartPaymentResult:
    with _billing_lock(user.pk):
        subscription = (
            ClubSubscription.objects.select_for_update()
            .filter(user=user, provider=ClubSubscription.PROVIDER_YOOKASSA)
            .first()
        )
        if subscription is None or not subscription.grants_access():
            return StartPaymentResult(
                False, 'not_found', 'Активной подписки ЮKassa нет.', http_status=404,
            )
        if subscription.plan != ClubSubscription.PLAN_MONTHLY:
            return StartPaymentResult(
                False,
                'not_monthly',
                'Годовая подписка без автопродления — отменять нечего.',
                http_status=400,
            )
        if not subscription.auto_renew:
            return StartPaymentResult(True, message='Автопродление уже отключено.')
        subscription.auto_renew = False
        subscription.cancelled_at = timezone.now()
        subscription.next_charge_at = None
        subscription.status = ClubSubscription.STATUS_CANCELLED
        subscription.save(update_fields=[
            'auto_renew', 'cancelled_at', 'next_charge_at', 'status', 'updated_at',
        ])
        transaction.on_commit(lambda: logger.info(
            'subscription_auto_renew_disabled user_id=%s subscription_id=%s reason=cancel',
            user.pk, subscription.pk,
        ))
    logger.info('subscription_cancel_requested user_id=%s subscription_id=%s', user.pk, subscription.pk)
    return StartPaymentResult(True, message='Автопродление отключено.')


def resume_yookassa_subscription(user) -> StartPaymentResult:
    with _billing_lock(user.pk):
        subscription = (
            ClubSubscription.objects.select_for_update()
            .filter(user=user, provider=ClubSubscription.PROVIDER_YOOKASSA)
            .first()
        )
        if subscription is None or not subscription.grants_access():
            return StartPaymentResult(
                False, 'not_found', 'Активной подписки ЮKassa нет.', http_status=404,
            )
        if subscription.plan != ClubSubscription.PLAN_MONTHLY:
            return StartPaymentResult(
                False, 'not_monthly', 'Возобновление доступно только для месячной подписки.',
                http_status=400,
            )
        if not _active_method(subscription) or subscription.payment_method_save_failed:
            return StartPaymentResult(
                False,
                'no_payment_method',
                'Сохранённый способ оплаты недоступен. Оформите подписку заново с новой привязкой карты.',
                http_status=409,
            )
        if subscription.auto_renew:
            return StartPaymentResult(True, message='Автопродление уже включено.')
        subscription.auto_renew = True
        subscription.cancelled_at = None
        subscription.next_charge_at = subscription.paid_until
        subscription.status = ClubSubscription.STATUS_ACTIVE
        subscription.save(update_fields=[
            'auto_renew', 'cancelled_at', 'next_charge_at', 'status', 'updated_at',
        ])
    logger.info('subscription_resume_requested user_id=%s subscription_id=%s', user.pk, subscription.pk)
    return StartPaymentResult(True, message='Автопродление включено.')


def _expected_amount(local: ClubYooKassaPayment) -> int:
    return int(local.amount)


def _apply_succeeded_payment(local: ClubYooKassaPayment, payment_data: dict) -> None:
    if payment_data.get('status') != 'succeeded':
        return
    amount_obj = payment_data.get('amount') or {}
    paid_value = amount_obj.get('value')
    paid_currency = str(amount_obj.get('currency') or '').upper()
    try:
        paid_kopecks = int(Decimal(str(paid_value)) * 100)
    except Exception:
        paid_kopecks = -1
    if paid_currency != CURRENCY or paid_kopecks != _expected_amount(local):
        logger.error(
            'subscription_payment_amount_mismatch payment_pk=%s expected=%s got=%s %s',
            local.pk, local.amount, paid_kopecks, paid_currency,
        )
        local.failure_code = 'amount_mismatch'
        local.save(update_fields=['failure_code', 'updated_at'])
        return

    now = timezone.now()
    subscription = local.club_subscription
    period_start = local.period_start or now
    period_end = local.period_end
    if period_end is None:
        if local.kind == ClubYooKassaPayment.KIND_ANNUAL:
            period_end = add_calendar_years(period_start, 1)
        else:
            period_end = add_calendar_months(period_start, 1)

    local.status = ClubYooKassaPayment.STATUS_SUCCEEDED
    local.succeeded_at = now
    local.save(update_fields=['status', 'succeeded_at', 'updated_at'])

    subscription.provider = ClubSubscription.PROVIDER_YOOKASSA
    subscription.currency = CURRENCY
    subscription.amount = local.amount
    subscription.current_period_start = period_start
    if subscription.paid_until is None or period_end > subscription.paid_until:
        subscription.paid_until = period_end
    subscription.last_payment_at = now
    subscription.last_webhook_at = now
    subscription.last_webhook_event = 'payment.succeeded'
    subscription.payment_method_save_failed = False

    if local.kind == ClubYooKassaPayment.KIND_ANNUAL:
        subscription.plan = ClubSubscription.PLAN_ANNUAL
        subscription.auto_renew = False
        subscription.next_charge_at = None
        subscription.status = ClubSubscription.STATUS_CANCELLED  # paid, no renew
        # Display: cancelled means no auto-renew but grants_access while paid_until
        subscription.cancelled_at = subscription.cancelled_at or now
    else:
        subscription.plan = ClubSubscription.PLAN_MONTHLY
        pm = payment_data.get('payment_method') or {}
        saved = pm.get('saved') is True
        pm_id = str(pm.get('id') or '').strip()
        if local.kind == ClubYooKassaPayment.KIND_INITIAL_MONTHLY and local.amount == AMOUNT_INTRO_KOPECKS:
            if subscription.intro_offer_used_at is None:
                subscription.intro_offer_used_at = now
        initial = local.kind == ClubYooKassaPayment.KIND_INITIAL_MONTHLY
        detached_since_creation = bool(
            subscription.payment_method_detached_at
            and local.created_at <= subscription.payment_method_detached_at
        )
        cancelled_since_creation = bool(
            subscription.cancelled_at and local.created_at <= subscription.cancelled_at
        )
        if (initial and not detached_since_creation and not cancelled_since_creation
                and saved and pm_id and len(pm_id) <= 64 and pm.get('type') == 'bank_card'):
            card = pm.get('card') or {}
            last4 = card.get('last4', '')
            last4 = last4 if isinstance(last4, str) and re.fullmatch(r'[0-9]{4}', last4) else ''
            method = _active_method(subscription)
            if method is None:
                method = SavedPaymentMethod(user_id=subscription.user_id)
            method.provider_payment_method_id = pm_id
            method.card_last4 = last4
            method.save()
            subscription.saved_payment_method = method
            transaction.on_commit(lambda: logger.info(
                'payment_method_saved user_id=%s saved_payment_method_id=%s',
                subscription.user_id, method.pk,
            ))
            subscription.auto_renew = True
            subscription.next_charge_at = subscription.paid_until
            subscription.cancelled_at = None
            subscription.status = ClubSubscription.STATUS_ACTIVE
        elif not initial and subscription.auto_renew and _active_method(subscription):
            # A recurring webhook never saves a credential or reverses cancellation.
            subscription.next_charge_at = subscription.paid_until
            subscription.status = ClubSubscription.STATUS_ACTIVE
        else:
            subscription.payment_method_save_failed = bool(
                initial and not detached_since_creation and not cancelled_since_creation
            )
            subscription.auto_renew = False
            subscription.next_charge_at = None
            subscription.status = ClubSubscription.STATUS_CANCELLED
            subscription.cancelled_at = subscription.cancelled_at or now
            if subscription.payment_method_save_failed:
                logger.warning('subscription_payment_method_not_saved payment_pk=%s', local.pk)

    subscription.save()
    logger.info(
        'subscription_payment_succeeded payment_pk=%s user_id=%s kind=%s paid_until=%s',
        local.pk, subscription.user_id, local.kind, subscription.paid_until,
    )
    if local.kind == ClubYooKassaPayment.KIND_RECURRING_MONTHLY:
        logger.info('subscription_renewed subscription_id=%s', subscription.pk)
    else:
        logger.info('subscription_activated subscription_id=%s kind=%s', subscription.pk, local.kind)


def _apply_canceled_payment(local: ClubYooKassaPayment, payment_data: dict) -> None:
    cancellation = payment_data.get('cancellation_details') or {}
    local.status = ClubYooKassaPayment.STATUS_CANCELED
    local.failure_code = str(cancellation.get('party') or '')[:64]
    local.cancellation_reason = str(cancellation.get('reason') or '')[:255]
    local.save(update_fields=['status', 'failure_code', 'cancellation_reason', 'updated_at'])
    subscription = local.club_subscription
    subscription.last_webhook_at = timezone.now()
    subscription.last_webhook_event = 'payment.canceled'
    if local.kind == ClubYooKassaPayment.KIND_RECURRING_MONTHLY:
        # Keep paid access; mark past_due only after period ends (handled in renew scan / effective_status)
        if not subscription.grants_access():
            subscription.status = ClubSubscription.STATUS_PAST_DUE
            subscription.save(update_fields=['status', 'last_webhook_at', 'last_webhook_event', 'updated_at'])
        else:
            subscription.save(update_fields=['last_webhook_at', 'last_webhook_event', 'updated_at'])
    elif subscription.status == ClubSubscription.STATUS_PENDING and not subscription.grants_access():
        subscription.status = ClubSubscription.STATUS_EXPIRED
        subscription.save(update_fields=['status', 'last_webhook_at', 'last_webhook_event', 'updated_at'])
    else:
        subscription.save(update_fields=['last_webhook_at', 'last_webhook_event', 'updated_at'])
    logger.info(
        'subscription_payment_failed payment_pk=%s kind=%s reason=%s',
        local.pk, local.kind, local.cancellation_reason,
    )


def process_yookassa_club_payment_event(event_name: str, payment_data: dict) -> bool:
    """Handle club payment webhook. Returns True if this event was a club payment."""
    metadata = payment_data.get('metadata') or {}
    if str(metadata.get('purpose') or '') != METADATA_PURPOSE:
        return False

    payment_id = str(payment_data.get('id') or '').strip()
    local_id = metadata.get('subscription_payment_id')
    local = None
    if str(local_id or '').isdigit():
        local = ClubYooKassaPayment.objects.filter(pk=local_id).first()
    if local is None and payment_id:
        local = ClubYooKassaPayment.objects.filter(yookassa_payment_id=payment_id).first()
    if local is None:
        logger.warning('subscription_payment_unknown')
        return True
    with _billing_lock(local.user_id):
        subscription = ClubSubscription.objects.select_for_update().get(pk=local.club_subscription_id)
        local = ClubYooKassaPayment.objects.select_for_update().get(pk=local.pk)
        local.club_subscription = subscription

        if payment_id and local.yookassa_payment_id and local.yookassa_payment_id != payment_id:
            logger.error(
                'subscription_payment_id_mismatch local=%s', local.pk,
            )
            return True
        if payment_id and not local.yookassa_payment_id:
            local.yookassa_payment_id = payment_id
            local.save(update_fields=['yookassa_payment_id', 'updated_at'])

        if local.status == ClubYooKassaPayment.STATUS_SUCCEEDED:
            return True
        if local.status == ClubYooKassaPayment.STATUS_CANCELED and event_name == 'payment.canceled':
            return True

        if event_name == 'payment.succeeded':
            _apply_succeeded_payment(local, payment_data)
        elif event_name == 'payment.canceled' and payment_data.get('status') == 'canceled':
            _apply_canceled_payment(local, payment_data)
    return True


def renew_due_subscriptions(*, limit: int = 50) -> dict:
    """Create recurring charges for due monthly Club subscriptions."""
    stats = {'due': 0, 'created': 0, 'skipped': 0, 'errors': 0}
    if not yookassa_recurring_enabled():
        logger.info('subscription_renewal_skipped recurring_disabled')
        return stats

    now = timezone.now()
    due_ids = list(
        ClubSubscription.objects.filter(
            provider=ClubSubscription.PROVIDER_YOOKASSA,
            plan=ClubSubscription.PLAN_MONTHLY,
            auto_renew=True,
            next_charge_at__lte=now,
        )
        .filter(saved_payment_method__is_active=True,
                saved_payment_method__provider_payment_method_id__isnull=False)
        .filter(payment_method_save_failed=False)
        .order_by('next_charge_at')
        .values_list('pk', flat=True)[:limit]
    )
    stats['due'] = len(due_ids)

    for sub_id in due_ids:
        try:
            created = _renew_one(sub_id, now=now)
            if created:
                stats['created'] += 1
            else:
                stats['skipped'] += 1
        except Exception:
            stats['errors'] += 1
            logger.error('subscription_renewal_error subscription_id=%s', sub_id)
    return stats


def _renew_one(subscription_id: int, *, now) -> bool:
    user_id = ClubSubscription.objects.filter(pk=subscription_id).values_list('user_id', flat=True).first()
    if user_id is None:
        return False
    with _billing_lock(user_id):
        subscription = (
            ClubSubscription.objects.select_for_update()
            .filter(pk=subscription_id)
            .first()
        )
        if subscription is None:
            return False
        method = _active_method(subscription)
        if not (
            subscription.provider == ClubSubscription.PROVIDER_YOOKASSA
            and subscription.plan == ClubSubscription.PLAN_MONTHLY
            and
            subscription.auto_renew
            and subscription.next_charge_at
            and subscription.next_charge_at <= now
            and method
            and not subscription.payment_method_save_failed
        ):
            return False
        if not yookassa_recurring_enabled():
            return False

        period_start = subscription.paid_until or now
        period_end = add_calendar_months(period_start, 1)
        period_key = period_key_for(ClubYooKassaPayment.KIND_RECURRING_MONTHLY, period_start)
        if ClubYooKassaPayment.objects.filter(
            club_subscription=subscription, period_key=period_key,
        ).exists():
            return False

        local = ClubYooKassaPayment.objects.create(
            club_subscription=subscription,
            user_id=subscription.user_id,
            kind=ClubYooKassaPayment.KIND_RECURRING_MONTHLY,
            period_start=period_start,
            period_end=period_end,
            period_key=period_key,
            amount=AMOUNT_MONTHLY_KOPECKS,
            currency=CURRENCY,
            status=ClubYooKassaPayment.STATUS_PENDING,
            idempotency_key=uuid.uuid4().hex,
            submitted_at=now,
        )
        # Push next_charge_at forward so parallel workers skip until this attempt settles.
        subscription.next_charge_at = now + timedelta(days=1)
        subscription.save(update_fields=['next_charge_at', 'updated_at'])

    # Commit the attempt before contacting the provider, so even process death
    # cannot erase the only local evidence of a potentially submitted charge.
    return _submit_renewal(local.pk)


def _submit_renewal(payment_pk):
    attempt = ClubYooKassaPayment.objects.get(pk=payment_pk)
    with _billing_lock(attempt.user_id):
        subscription = ClubSubscription.objects.select_for_update().get(pk=attempt.club_subscription_id)
        local = ClubYooKassaPayment.objects.select_for_update().get(pk=payment_pk)
        local.club_subscription = subscription
        method = _active_method(subscription)
        if not (local.status == ClubYooKassaPayment.STATUS_PENDING
                and subscription.auto_renew and method and yookassa_recurring_enabled()
                and not (subscription.payment_method_detached_at
                         and local.created_at <= subscription.payment_method_detached_at)
                and subscription.provider == ClubSubscription.PROVIDER_YOOKASSA
                and subscription.plan == ClubSubscription.PLAN_MONTHLY):
            if local.status == ClubYooKassaPayment.STATUS_PENDING:
                local.status = ClubYooKassaPayment.STATUS_CANCELED
                local.failure_code = 'renewal_disabled_before_submission'
                local.save(update_fields=['status', 'failure_code', 'updated_at'])
            return False
        subscription_id = subscription.pk
        # Keep the lock until the SDK has returned (including network retries).
        # Do not log exception contents: SDK errors may contain request tokens.
        try:
            payment_data = _create_yookassa_payment(
                local_payment=local,
                amount_kopecks=AMOUNT_MONTHLY_KOPECKS,
                description='Подписка Inter Oves — 1 месяц',
                save_payment_method=False,
                return_url='',
                payment_method_id=method.provider_payment_method_id,
            )
        except Exception:
            logger.error(
                'subscription_renewal_submission_unknown subscription_id=%s payment_pk=%s',
                subscription_id, local.pk,
            )
            # Timeout is NOT proof of cancellation. Keep an unresolved attempt;
            # never retry with a new key, and disclose it on detachment.
            local.failure_code = 'submission_unknown'
            local.save(update_fields=['failure_code', 'updated_at'])
            return False

        local.yookassa_payment_id = payment_data.get('id') or ''
        local.save(update_fields=['yookassa_payment_id', 'updated_at'])
        logger.info('subscription_renewal_created subscription_id=%s payment_pk=%s',
                    subscription_id, local.pk)
        status = str(payment_data.get('status') or '')
        if status == 'succeeded':
            _apply_succeeded_payment(local, payment_data)
        elif status == 'canceled':
            _apply_canceled_payment(local, payment_data)
    return True
