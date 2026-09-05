"""Tribute recurring Club subscription webhook processing.

Creator subscription events (not Shop API) share the existing /tribute/webhook/
endpoint, HMAC verification, and Profile.telegram_user_id mapping.

Payload fields follow Tribute's creator subscription schema as used by the
existing digital-product integration and independently confirmed by the
OpenDonationAssistant TributeSubscriptionPayload:

    subscription_id, period_id, period, type, price, amount, currency,
    trb_user_id, telegram_user_id, telegram_username, expires_at,
    subscription_name, channel_id, channel_name

There is no creator-webhook failed-renewal event. Tribute cancels the
subscription after failed charges; we then receive cancelled_subscription.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from games.models import ClubSubscription, ClubSubscriptionEvent, Profile
from games.tribute_config import club_products_by_id

logger = logging.getLogger(__name__)

SUBSCRIPTION_EVENTS = frozenset({
    'new_subscription',
    'renewed_subscription',
    'cancelled_subscription',
})
PAID_EVENTS = frozenset({'new_subscription', 'renewed_subscription'})
PAID_TYPES = frozenset({'regular', ''})
RETRYABLE_RESULTS = frozenset({
    ClubSubscriptionEvent.RESULT_UNMATCHED_TELEGRAM,
    ClubSubscriptionEvent.RESULT_IGNORED_PRODUCT,
    ClubSubscriptionEvent.RESULT_MALFORMED,
})


class ClubPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ClubProcessResult:
    event: ClubSubscriptionEvent
    subscription: ClubSubscription | None = None
    duplicate: bool = False


def _parse_optional_int(value, field: str, *, required=False) -> int | None:
    if value in (None, ''):
        if required:
            raise ClubPayloadError('Missing {}'.format(field))
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ClubPayloadError('Invalid {}'.format(field)) from exc
    if parsed <= 0:
        raise ClubPayloadError('Invalid {}'.format(field))
    return parsed


def _parse_event_datetime(value):
    if not value and value != 0:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raw = str(value).strip()
        if raw.isdigit():
            try:
                return datetime.fromtimestamp(int(raw), tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _safe_excerpt(data: dict, *, envelope_created_at=None) -> dict:
    expires = data.get('expires_at')
    if expires is not None:
        expires = expires.isoformat()
    envelope = envelope_created_at
    if envelope is not None:
        envelope = envelope.isoformat()
    return {
        'subscription_id': data.get('subscription_id'),
        'period_id': data.get('period_id'),
        'period': data.get('period'),
        'type': data.get('type'),
        'amount': data.get('amount'),
        'currency': data.get('currency'),
        'telegram_user_id': data.get('telegram_user_id'),
        'expires_at': expires,
        'envelope_created_at': envelope,
    }


def normalize_subscription_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ClubPayloadError('payload must be an object')
    amount = payload.get('amount')
    if amount in (None, ''):
        amount = payload.get('price')
    normalized = {
        'subscription_id': _parse_optional_int(payload.get('subscription_id'), 'subscription_id', required=True),
        'subscription_name': str(payload.get('subscription_name') or '')[:255],
        'period_id': _parse_optional_int(payload.get('period_id'), 'period_id'),
        'period': str(payload.get('period') or '').strip().lower()[:32],
        'type': str(payload.get('type') or '').strip().lower()[:32],
        'amount': _parse_optional_int(amount, 'amount'),
        'currency': str(payload.get('currency') or '').strip().upper(),
        'trb_user_id': str(payload.get('trb_user_id') or '')[:128],
        'telegram_user_id': _parse_optional_int(
            payload.get('telegram_user_id'), 'telegram_user_id', required=False,
        ),
        'telegram_username': str(payload.get('telegram_username') or '').strip().lstrip('@')[:64],
        'expires_at': _parse_event_datetime(payload.get('expires_at')),
    }
    if normalized['currency'] and len(normalized['currency']) > 3:
        raise ClubPayloadError('Invalid currency')
    return normalized


def _event_key(event_name: str, data: dict, envelope_created_at) -> str:
    expires = ''
    if data.get('expires_at') is not None:
        expires = data['expires_at'].isoformat()
    created = ''
    if envelope_created_at is not None:
        created = envelope_created_at.isoformat()
    return '{}:{}:{}:{}:{}:{}'.format(
        event_name,
        data.get('subscription_id') or 0,
        data.get('period_id') or 0,
        data.get('telegram_user_id') or 0,
        expires,
        created,
    )[:255]


def _derived_status(subscription: ClubSubscription, *, now=None) -> str:
    return subscription.effective_status(now=now)


def _queue_goal(subscription: ClubSubscription, kind: str, now) -> None:
    if kind == 'payment' and subscription.payment_success_goal_queued_at is None:
        subscription.payment_success_goal_queued_at = now
    elif kind == 'renewal' and subscription.renewal_goal_queued_at is None:
        subscription.renewal_goal_queued_at = now
    elif kind == 'cancelled' and subscription.cancelled_goal_queued_at is None:
        subscription.cancelled_goal_queued_at = now


def process_subscription_event(event_name: str, payload: dict, *, envelope_created_at=None) -> ClubProcessResult:
    data = normalize_subscription_payload(payload)
    now = timezone.now()
    created_at = envelope_created_at or now
    event_key = _event_key(event_name, data, created_at)

    with transaction.atomic():
        event, created = ClubSubscriptionEvent.objects.get_or_create(
            event_key=event_key,
            defaults={
                'event_name': event_name,
                'tribute_subscription_id': data['subscription_id'],
                'tribute_period_id': data['period_id'],
                'telegram_user_id': data['telegram_user_id'],
                'result': ClubSubscriptionEvent.RESULT_MALFORMED,
                'expires_at': data['expires_at'],
                'payload_excerpt': _safe_excerpt(data, envelope_created_at=created_at),
            },
        )
        event = ClubSubscriptionEvent.objects.select_for_update().get(pk=event.pk)
        if not created and event.result not in RETRYABLE_RESULTS:
            logger.info(
                'tribute_club_duplicate event=%s subscription_id=%s telegram_user_id=%s',
                event_name, data['subscription_id'], data['telegram_user_id'],
            )
            return ClubProcessResult(event, event.club_subscription, duplicate=True)
        if not created:
            logger.info(
                'tribute_club_retry event=%s previous_result=%s subscription_id=%s',
                event_name, event.result, data['subscription_id'],
            )

        product = club_products_by_id().get(data['subscription_id'])
        if product is None:
            event.result = ClubSubscriptionEvent.RESULT_IGNORED_PRODUCT
            event.save(update_fields=['result'])
            logger.info(
                'tribute_club_ignored_product event=%s subscription_id=%s',
                event_name, data['subscription_id'],
            )
            return ClubProcessResult(event)

        paid_type = (data['type'] or 'regular')
        if event_name in PAID_EVENTS and paid_type not in PAID_TYPES:
            event.result = ClubSubscriptionEvent.RESULT_IGNORED_TYPE
            event.save(update_fields=['result'])
            logger.info(
                'tribute_club_ignored_type event=%s type=%s subscription_id=%s',
                event_name, data['type'], data['subscription_id'],
            )
            return ClubProcessResult(event)

        if data['telegram_user_id'] is None:
            event.result = ClubSubscriptionEvent.RESULT_UNMATCHED_TELEGRAM
            event.save(update_fields=['result'])
            logger.warning(
                'tribute_club_missing_telegram event=%s subscription_id=%s',
                event_name, data['subscription_id'],
            )
            return ClubProcessResult(event)

        profile = (
            Profile.objects.select_for_update()
            .select_related('user')
            .filter(telegram_user_id=data['telegram_user_id'], telegram_verified=True)
            .first()
        )
        if profile is None:
            event.result = ClubSubscriptionEvent.RESULT_UNMATCHED_TELEGRAM
            event.save(update_fields=['result'])
            logger.warning(
                'tribute_club_unknown_telegram event=%s telegram_user_id=%s subscription_id=%s',
                event_name, data['telegram_user_id'], data['subscription_id'],
            )
            return ClubProcessResult(event)

        if data['telegram_username'] and profile.telegram_username != data['telegram_username']:
            profile.telegram_username = data['telegram_username']
            profile.save(update_fields=['telegram_username'])

        if event_name in PAID_EVENTS:
            if data['amount'] != product.amount or data['currency'] != product.currency:
                event.result = ClubSubscriptionEvent.RESULT_MALFORMED
                event.save(update_fields=['result'])
                logger.warning(
                    'tribute_club_amount_mismatch event=%s subscription_id=%s amount=%s currency=%s',
                    event_name, data['subscription_id'], data['amount'], data['currency'],
                )
                return ClubProcessResult(event)
            if data['expires_at'] is None:
                event.result = ClubSubscriptionEvent.RESULT_MALFORMED
                event.save(update_fields=['result'])
                logger.warning(
                    'tribute_club_missing_expires_at event=%s subscription_id=%s',
                    event_name, data['subscription_id'],
                )
                return ClubProcessResult(event)

        subscription, _created_sub = ClubSubscription.objects.select_for_update().get_or_create(
            user=profile.user,
            defaults={
                'provider': ClubSubscription.PROVIDER_TRIBUTE,
                'status': ClubSubscription.STATUS_EXPIRED,
                'telegram_user_id': data['telegram_user_id'],
            },
        )

        if (
            subscription.last_event_created_at is not None
            and created_at < subscription.last_event_created_at
        ):
            event.result = ClubSubscriptionEvent.RESULT_DELAYED
            event.club_subscription = subscription
            event.save(update_fields=['result', 'club_subscription'])
            logger.info(
                'tribute_club_delayed event=%s user_id=%s subscription_id=%s',
                event_name, profile.user_id, data['subscription_id'],
            )
            return ClubProcessResult(event, subscription)

        anomaly = False
        if (
            event_name in PAID_EVENTS
            and subscription.tribute_subscription_id
            and subscription.tribute_subscription_id != data['subscription_id']
            and subscription.grants_access(now)
        ):
            anomaly = True
            subscription.duplicate_detected = True
            logger.warning(
                'tribute_club_duplicate_subscription user_id=%s existing_id=%s incoming_id=%s',
                profile.user_id, subscription.tribute_subscription_id, data['subscription_id'],
            )

        subscription.telegram_user_id = data['telegram_user_id']
        subscription.last_webhook_at = now
        subscription.last_webhook_event = event_name
        subscription.last_event_created_at = created_at

        if event_name in PAID_EVENTS:
            incoming_end = data['expires_at']
            if subscription.paid_until is None or incoming_end > subscription.paid_until:
                subscription.paid_until = incoming_end
            subscription.tribute_subscription_id = data['subscription_id']
            subscription.tribute_period_id = data['period_id']
            subscription.currency = data['currency']
            subscription.amount = data['amount']
            subscription.auto_renew = True
            subscription.cancelled_at = None
            subscription.last_payment_at = now
            _queue_goal(
                subscription,
                'renewal' if event_name == 'renewed_subscription' else 'payment',
                now,
            )
        elif event_name == 'cancelled_subscription':
            subscription.auto_renew = False
            if subscription.cancelled_at is None:
                subscription.cancelled_at = now
            if data['expires_at'] is not None:
                if subscription.paid_until is None or data['expires_at'] > subscription.paid_until:
                    subscription.paid_until = data['expires_at']
            _queue_goal(subscription, 'cancelled', now)

        subscription.status = _derived_status(subscription, now=now)
        subscription.save()
        event.club_subscription = subscription
        event.result = (
            ClubSubscriptionEvent.RESULT_ANOMALY if anomaly else ClubSubscriptionEvent.RESULT_APPLIED
        )
        event.save(update_fields=['club_subscription', 'result'])
        logger.info(
            'tribute_club_applied event=%s user_id=%s subscription_id=%s status=%s paid_until=%s',
            event_name, profile.user_id, data['subscription_id'],
            subscription.status, subscription.paid_until,
        )
        return ClubProcessResult(event, subscription)


def _payload_from_excerpt(event: ClubSubscriptionEvent) -> dict:
    excerpt = event.payload_excerpt if isinstance(event.payload_excerpt, dict) else {}
    payload = {
        'subscription_id': excerpt.get('subscription_id') or event.tribute_subscription_id,
        'period_id': excerpt.get('period_id') or event.tribute_period_id,
        'period': excerpt.get('period') or '',
        'type': excerpt.get('type') or 'regular',
        'amount': excerpt.get('amount'),
        'price': excerpt.get('amount'),
        'currency': excerpt.get('currency') or '',
        'telegram_user_id': excerpt.get('telegram_user_id') or event.telegram_user_id,
        'expires_at': excerpt.get('expires_at'),
    }
    return payload


def apply_pending_club_events_for_telegram(telegram_user_id) -> int:
    """Re-apply unmatched Club webhooks after a Telegram account is linked."""
    try:
        numeric_id = int(telegram_user_id)
    except (TypeError, ValueError):
        return 0
    if numeric_id <= 0:
        return 0
    events = list(
        ClubSubscriptionEvent.objects.filter(
            telegram_user_id=numeric_id,
            result=ClubSubscriptionEvent.RESULT_UNMATCHED_TELEGRAM,
        ).order_by('received_at')
    )
    applied = 0
    for event in events:
        excerpt = event.payload_excerpt if isinstance(event.payload_excerpt, dict) else {}
        envelope = _parse_event_datetime(excerpt.get('envelope_created_at'))
        result = process_subscription_event(
            event.event_name,
            _payload_from_excerpt(event),
            envelope_created_at=envelope,
        )
        if result.subscription is not None and result.event.result in (
            ClubSubscriptionEvent.RESULT_APPLIED,
            ClubSubscriptionEvent.RESULT_ANOMALY,
        ):
            applied += 1
    if applied:
        logger.info(
            'tribute_club_applied_after_telegram_link telegram_user_id=%s applied=%s',
            numeric_id, applied,
        )
    return applied
