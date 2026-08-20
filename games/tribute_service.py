"""Idempotent Tribute Digital Product intent, matching, issue and refund services."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.models import (
    Profile,
    ProfileTeamMembership,
    Team,
    TicketRequest,
    TributePaymentIntent,
    TributePurchase,
)
from games.ticket_service import accept_ticket_request, reject_ticket_request
from games.tribute_config import (
    TributeProduct,
    configured_product,
    merchant,
    products_by_id,
    tribute_checkout_enabled,
)

logger = logging.getLogger(__name__)


class TributePayloadError(ValueError):
    pass


class TributeCheckoutError(Exception):
    def __init__(self, reason: str, message: str, status: int = 400):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status


@dataclass(frozen=True)
class TributeProcessResult:
    purchase: TributePurchase
    duplicate: bool = False
    ticket_issued: bool = False


def team_is_discount_eligible(team: Team) -> bool:
    """Current Inter Oves discount truth: confirmed teams have the 500 RUB tariff."""
    try:
        return int(team.ticket_price) == 500
    except (TypeError, ValueError):
        return False


def _parse_positive_int(value, field: str, *, required=True) -> int | None:
    if value in (None, ''):
        if required:
            raise TributePayloadError('Missing {}'.format(field))
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TributePayloadError('Invalid {}'.format(field)) from exc
    if parsed <= 0:
        raise TributePayloadError('Invalid {}'.format(field))
    return parsed


def _parse_event_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def normalize_purchase_payload(payload: dict, *, refund=False) -> dict:
    if not isinstance(payload, dict):
        raise TributePayloadError('payload must be an object')
    normalized = {
        'product_id': _parse_positive_int(payload.get('product_id'), 'product_id'),
        'product_name': str(payload.get('product_name') or '')[:255],
        'amount': _parse_positive_int(payload.get('amount'), 'amount'),
        'currency': str(payload.get('currency') or '').strip().upper(),
        'trb_user_id': str(payload.get('trb_user_id') or '')[:128],
        'telegram_user_id': _parse_positive_int(
            payload.get('telegram_user_id'), 'telegram_user_id', required=False,
        ),
        'telegram_username': str(payload.get('telegram_username') or '').strip().lstrip('@')[:64],
        'purchase_id': str(payload.get('purchase_id') or '').strip()[:128],
        'transaction_id': str(payload.get('transaction_id') or '').strip()[:128],
    }
    if not normalized['purchase_id']:
        raise TributePayloadError('Missing purchase_id')
    if not normalized['transaction_id']:
        raise TributePayloadError('Missing transaction_id')
    if not normalized['currency'] or len(normalized['currency']) > 3:
        raise TributePayloadError('Invalid currency')
    if refund:
        normalized['refund_reason'] = str(payload.get('refund_reason') or '')[:255]
        normalized['refunded_at'] = str(payload.get('refunded_at') or '')
        if _parse_event_datetime(normalized['refunded_at']) is None:
            raise TributePayloadError('Missing or invalid refunded_at')
    else:
        normalized['purchase_created_at'] = str(payload.get('purchase_created_at') or '')
        if _parse_event_datetime(normalized['purchase_created_at']) is None:
            raise TributePayloadError('Missing or invalid purchase_created_at')
    return normalized


def resolve_authorized_team(user, raw_team_id) -> Team:
    team_id = str(raw_team_id or '').strip()
    if not team_id:
        raise TributeCheckoutError('team', 'Выберите команду для начисления билета.')
    membership = (
        ProfileTeamMembership.objects.select_related('team')
        .filter(profile__user=user, team_id=team_id)
        .first()
    )
    if membership is None:
        raise TributeCheckoutError('team_forbidden', 'Нельзя начислить билет выбранной команде.', 403)
    return membership.team


def create_or_reuse_intent(*, user, team: Team) -> tuple[TributePaymentIntent, TributeProduct, bool]:
    if not tribute_checkout_enabled():
        raise TributeCheckoutError('tribute_config', 'Оплата через Tribute пока не настроена.', 503)

    now = timezone.now()
    with transaction.atomic():
        profile = Profile.objects.select_for_update().filter(user=user).first()
        if not profile or not profile.telegram_verified or not profile.telegram_user_id:
            raise TributeCheckoutError(
                'telegram_unlinked',
                'Сначала привяжите Telegram, чтобы Tribute мог автоматически начислить билет.',
                409,
            )
        locked_team = Team.objects.select_for_update().get(pk=team.pk)
        kind = 'discount' if team_is_discount_eligible(locked_team) else 'regular'
        product = configured_product(kind)
        if product is None:
            raise TributeCheckoutError('tribute_config', 'Товар Tribute не настроен.', 503)

        TributePaymentIntent.objects.filter(
            telegram_user_id=profile.telegram_user_id,
            status=TributePaymentIntent.STATUS_AWAITING,
            expires_at__lte=now,
        ).update(status=TributePaymentIntent.STATUS_EXPIRED)

        active = (
            TributePaymentIntent.objects.select_for_update()
            .select_related('ticket_request')
            .filter(
                telegram_user_id=profile.telegram_user_id,
                expected_product_id=product.product_id,
                status=TributePaymentIntent.STATUS_AWAITING,
            )
            .first()
        )
        if active is not None:
            unchanged = (
                active.user_id == user.pk
                and active.team_id == locked_team.pk
                and active.expected_amount == product.amount
                and active.expected_currency == product.currency
            )
            if unchanged:
                return active, product, True
            active.status = TributePaymentIntent.STATUS_CANCELLED
            active.save(update_fields=['status'])
            reject_ticket_request(active.ticket_request, source='tribute_intent_replaced')

        ticket_request = TicketRequest.objects.create(
            team=locked_team,
            created_by=user,
            money=product.amount_major,
            tickets=1,
            status='Pending',
            currency=product.currency,
            payment_provider=TicketRequest.PROVIDER_TRIBUTE_DIGITAL,
            merchant=merchant(),
        )
        intent = TributePaymentIntent.objects.create(
            user=user,
            team=locked_team,
            ticket_request=ticket_request,
            telegram_user_id=profile.telegram_user_id,
            expected_product_id=product.product_id,
            expected_amount=product.amount,
            expected_currency=product.currency,
            ticket_type=kind,
            expires_at=now + timedelta(minutes=int(getattr(settings, 'TRIBUTE_INTENT_TTL_MINUTES', 120))),
        )
    logger.info(
        'tribute_intent_created intent_id=%s user_id=%s team_id=%s product_id=%s',
        intent.pk, user.pk, team.pk, product.product_id,
    )
    return intent, product, False


def _save_review(purchase: TributePurchase, reason: str, *, user=None) -> TributeProcessResult:
    purchase.status = TributePurchase.STATUS_MANUAL_REVIEW
    purchase.review_reason = reason
    if user is not None:
        purchase.matched_user = user
    purchase.save(update_fields=['status', 'review_reason', 'matched_user'])
    logger.warning(
        'tribute_purchase_unmatched purchase_id=%s product_id=%s reason=%s',
        purchase.purchase_id, purchase.product_id, reason,
    )
    return TributeProcessResult(purchase)


def process_new_purchase(payload: dict) -> TributeProcessResult:
    data = normalize_purchase_payload(payload)
    now = timezone.now()
    with transaction.atomic():
        purchase, created = TributePurchase.objects.get_or_create(
            purchase_id=data['purchase_id'],
            defaults={
                'transaction_id': data['transaction_id'],
                'product_id': data['product_id'],
                'product_name': data['product_name'],
                'amount': data['amount'],
                'currency': data['currency'],
                'trb_user_id': data['trb_user_id'],
                'telegram_user_id': data['telegram_user_id'],
                'telegram_username': data['telegram_username'],
                'purchase_created_at': _parse_event_datetime(data.get('purchase_created_at')),
                'normalized_payload': data,
            },
        )
        if not created:
            purchase = TributePurchase.objects.select_for_update().get(pk=purchase.pk)
            logger.info('tribute_duplicate_webhook purchase_id=%s', data['purchase_id'])
            return TributeProcessResult(purchase, duplicate=True)

        product = products_by_id().get(data['product_id'])
        if product is None:
            return _save_review(purchase, TributePurchase.REASON_UNKNOWN_PRODUCT)
        if data['amount'] != product.amount:
            return _save_review(purchase, TributePurchase.REASON_AMOUNT_MISMATCH)
        if data['currency'] != product.currency:
            return _save_review(purchase, TributePurchase.REASON_CURRENCY_MISMATCH)
        if data['telegram_user_id'] is None:
            return _save_review(purchase, TributePurchase.REASON_MISSING_TELEGRAM)

        profile = (
            Profile.objects.select_for_update()
            .select_related('user')
            .filter(telegram_user_id=data['telegram_user_id'], telegram_verified=True)
            .first()
        )
        if profile is None:
            return _save_review(purchase, TributePurchase.REASON_UNKNOWN_TELEGRAM)
        if data['telegram_username'] and profile.telegram_username != data['telegram_username']:
            profile.telegram_username = data['telegram_username']
            profile.save(update_fields=['telegram_username'])

        TributePaymentIntent.objects.filter(
            user=profile.user,
            telegram_user_id=data['telegram_user_id'],
            expected_product_id=data['product_id'],
            status=TributePaymentIntent.STATUS_AWAITING,
            expires_at__lte=now,
        ).update(status=TributePaymentIntent.STATUS_EXPIRED)
        intents = list(
            TributePaymentIntent.objects.select_for_update()
            .select_related('team', 'ticket_request')
            .filter(
                user=profile.user,
                telegram_user_id=data['telegram_user_id'],
                expected_product_id=data['product_id'],
                status=TributePaymentIntent.STATUS_AWAITING,
                expires_at__gt=now,
            )[:2]
        )
        if not intents:
            return _save_review(purchase, TributePurchase.REASON_NO_INTENT, user=profile.user)
        if len(intents) != 1:
            return _save_review(purchase, TributePurchase.REASON_MULTIPLE_INTENTS, user=profile.user)
        intent = intents[0]
        if intent.expected_amount != data['amount']:
            return _save_review(purchase, TributePurchase.REASON_AMOUNT_MISMATCH, user=profile.user)
        if intent.expected_currency != data['currency']:
            return _save_review(purchase, TributePurchase.REASON_CURRENCY_MISMATCH, user=profile.user)
        if intent.ticket_type == TributePaymentIntent.TYPE_DISCOUNT and not team_is_discount_eligible(intent.team):
            return _save_review(purchase, TributePurchase.REASON_DISCOUNT_INELIGIBLE, user=profile.user)

        ticket = TicketRequest.objects.select_for_update().select_related('team').get(pk=intent.ticket_request_id)
        issue_result = accept_ticket_request(
            ticket,
            tribute_id=data['purchase_id'],
            source='tribute_digital_webhook',
        )
        intent.status = TributePaymentIntent.STATUS_COMPLETED
        intent.completed_at = now
        intent.save(update_fields=['status', 'completed_at'])
        purchase.status = TributePurchase.STATUS_ISSUED
        purchase.review_reason = ''
        purchase.matched_user = profile.user
        purchase.matched_team = intent.team
        purchase.payment_intent = intent
        purchase.ticket_request = ticket
        purchase.processed_at = now
        purchase.save(update_fields=[
            'status', 'review_reason', 'matched_user', 'matched_team', 'payment_intent',
            'ticket_request', 'processed_at',
        ])
        logger.info(
            'tribute_purchase_matched purchase_id=%s intent_id=%s user_id=%s team_id=%s',
            purchase.purchase_id, intent.pk, profile.user_id, intent.team_id,
        )

    logger.info(
        'tribute_ticket_issued purchase_id=%s ticket_request_id=%s team_id=%s',
        purchase.purchase_id, ticket.pk, ticket.team_id,
    )
    return TributeProcessResult(purchase, ticket_issued=issue_result.changed)


def manually_issue_purchase(purchase_id: int) -> TributeProcessResult:
    """Staff-only caller: issue a reviewed valid purchase to preselected user/team."""
    now = timezone.now()
    with transaction.atomic():
        purchase = TributePurchase.objects.select_for_update().get(pk=purchase_id)
        if purchase.status == TributePurchase.STATUS_ISSUED:
            return TributeProcessResult(purchase, duplicate=True)
        if purchase.status == TributePurchase.STATUS_REFUNDED:
            raise TributeCheckoutError('refunded', 'Возвращенную покупку нельзя начислить.')
        product = products_by_id().get(purchase.product_id)
        if product is None or purchase.amount != product.amount or purchase.currency != product.currency:
            raise TributeCheckoutError('invalid_product', 'Product, amount или currency не совпадают с конфигурацией.')
        if not purchase.matched_user_id or not purchase.matched_team_id:
            raise TributeCheckoutError('manual_match_required', 'Сначала выберите пользователя и команду.')
        if not ProfileTeamMembership.objects.filter(
            profile__user_id=purchase.matched_user_id,
            team_id=purchase.matched_team_id,
        ).exists():
            raise TributeCheckoutError('team_forbidden', 'Пользователь не состоит в выбранной команде.')

        team = Team.objects.select_for_update().get(pk=purchase.matched_team_id)
        ticket = TicketRequest.objects.create(
            team=team,
            created_by_id=purchase.matched_user_id,
            money=product.amount_major,
            tickets=1,
            status='Pending',
            currency=product.currency,
            payment_provider=TicketRequest.PROVIDER_TRIBUTE_DIGITAL,
            merchant=merchant(),
            tribute_id=purchase.purchase_id,
        )
        issue_result = accept_ticket_request(ticket, source='tribute_manual_review')
        purchase.ticket_request = ticket
        purchase.status = TributePurchase.STATUS_ISSUED
        purchase.review_reason = ''
        purchase.processed_at = now
        purchase.save(update_fields=['ticket_request', 'status', 'review_reason', 'processed_at'])
    return TributeProcessResult(purchase, ticket_issued=issue_result.changed)


def process_refund(payload: dict) -> TributeProcessResult:
    data = normalize_purchase_payload(payload, refund=True)
    now = timezone.now()
    with transaction.atomic():
        purchase = TributePurchase.objects.select_for_update().filter(purchase_id=data['purchase_id']).first()
        if purchase is None:
            purchase = TributePurchase.objects.create(
                purchase_id=data['purchase_id'],
                transaction_id=data['transaction_id'],
                product_id=data['product_id'],
                product_name=data['product_name'],
                amount=data['amount'],
                currency=data['currency'],
                trb_user_id=data['trb_user_id'],
                telegram_user_id=data['telegram_user_id'],
                telegram_username=data['telegram_username'],
                normalized_payload=data,
                status=TributePurchase.STATUS_REFUNDED,
                review_reason=TributePurchase.REASON_ORIGINAL_NOT_FOUND,
                accounting_review_required=True,
            )
        elif purchase.status == TributePurchase.STATUS_REFUNDED:
            logger.info('tribute_duplicate_webhook refund purchase_id=%s', data['purchase_id'])
            return TributeProcessResult(purchase, duplicate=True)

        purchase.status = TributePurchase.STATUS_REFUNDED
        purchase.refund_reason = data.get('refund_reason', '')
        purchase.refunded_at = _parse_event_datetime(data.get('refunded_at')) or now

        if purchase.ticket_request_id:
            ticket = (
                TicketRequest.objects.select_for_update()
                .select_related('team')
                .get(pk=purchase.ticket_request_id)
            )
            if ticket.team_id and ticket.status == 'Accepted':
                team = Team.objects.select_for_update().get(pk=ticket.team_id)
                count = int(ticket.tickets or 0)
                if team.tickets >= count:
                    team.tickets -= count
                    team.save(update_fields=['tickets'])
                    purchase.ticket_revoked_at = now
                else:
                    # Tickets are fungible in the current model. A balance below the
                    # issued quantity means at least one was already consumed.
                    purchase.accounting_review_required = True
                    purchase.review_reason = TributePurchase.REASON_TICKET_USED
        purchase.save(update_fields=[
            'status', 'refund_reason', 'refunded_at', 'ticket_revoked_at',
            'accounting_review_required', 'review_reason',
        ])
    logger.info('tribute_refund_received purchase_id=%s', purchase.purchase_id)
    return TributeProcessResult(purchase)
