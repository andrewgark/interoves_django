"""Creation and redemption of one-time Club subscription gifts."""
from __future__ import annotations

import hashlib
import secrets
import base64
from dataclasses import dataclass
from datetime import timedelta
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from games.club_yookassa import add_calendar_months
from games.models import ClubEntitlement, ClubSubscription, Profile, SubscriptionGift

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode('ascii')).hexdigest()


def _new_code() -> str:
    raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return 'IO-{}-{}'.format(raw[:4], raw[4:])


def _code_cipher():
    key = hashlib.sha256(str(settings.SUBSCRIPTION_GIFT_ENCRYPTION_KEY).encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_gift_code(code: str) -> str:
    return _code_cipher().encrypt(code.encode('ascii')).decode('ascii')


def decrypt_gift_code(gift: SubscriptionGift) -> str:
    if not gift.code_ciphertext:
        return ''
    try:
        return _code_cipher().decrypt(gift.code_ciphertext.encode('ascii')).decode('ascii')
    except (InvalidToken, UnicodeDecodeError):
        return ''


def _gift_duration_label(gift: SubscriptionGift) -> str:
    if gift.is_forever:
        return 'навсегда'

    months = gift.duration_months
    months_mod_100 = months % 100
    if 11 <= months_mod_100 <= 14:
        word = 'месяцев'
    elif months % 10 == 1:
        word = 'месяц'
    elif 2 <= months % 10 <= 4:
        word = 'месяца'
    else:
        word = 'месяцев'
    return '{} {}'.format(months, word)


@dataclass(frozen=True)
class CreatedGift:
    gift: SubscriptionGift
    code: str


def create_gift(*, created_by, recipient_telegram_user_id: int | None = None, recipient_telegram_username: str = '',
               duration_months: int | None = None, is_forever: bool = False,
               created_by_telegram_user_id: int | None = None) -> CreatedGift:
    if is_forever == (duration_months is not None):
        raise ValueError('Укажите либо количество месяцев, либо forever')
    if duration_months is not None and not 1 <= duration_months <= 120:
        raise ValueError('Срок подарка должен быть от 1 до 120 месяцев')

    for _ in range(5):
        code = _new_code()
        try:
            gift = SubscriptionGift.objects.create(
                recipient_telegram_user_id=(
                    int(recipient_telegram_user_id)
                    if recipient_telegram_user_id is not None else None
                ),
                recipient_telegram_username=(recipient_telegram_username or '').strip().lstrip('@')[:64],
                code_hash=_code_hash(code),
                duration_months=duration_months,
                is_forever=is_forever,
                created_by=created_by,
                created_by_telegram_user_id=created_by_telegram_user_id,
                code_ciphertext=encrypt_gift_code(code),
            )
            return CreatedGift(gift=gift, code=code)
        except Exception:
            if not SubscriptionGift.objects.filter(code_hash=_code_hash(code)).exists():
                raise
    raise RuntimeError('Не удалось создать уникальный код подарка')


def create_paid_gift(*, purchaser, duration_months: int, provider: str, amount: int, currency: str):
    if duration_months not in (1, 3):
        raise ValueError('Подарочную подписку можно купить на 1 или 3 месяца')
    code = _new_code()
    for _ in range(5):
        try:
            with transaction.atomic():
                gift = SubscriptionGift.objects.create(
                    duration_months=duration_months,
                    is_forever=False,
                    status=SubscriptionGift.STATUS_CREATED,
                    created_by=purchaser,
                    purchaser=purchaser,
                    provider=provider,
                    code_hash=_code_hash(code),
                    code_ciphertext=encrypt_gift_code(code),
                    expires_at=timezone.now() + timedelta(days=365),
                )
            return gift, code
        except IntegrityError:
            code = _new_code()
    raise RuntimeError('Не удалось создать уникальный код подарка')


def _current_access_end(user, now):
    subscription = ClubSubscription.objects.select_for_update().filter(user=user).first()
    entitlement_qs = ClubEntitlement.objects.filter(
        user=user, revoked_at__isnull=True, starts_at__lte=now,
    )
    if subscription is not None:
        entitlement_qs = entitlement_qs.filter(
            kind__in=(ClubEntitlement.KIND_GIFT, ClubEntitlement.KIND_MANUAL),
        )
    entitlement_end = entitlement_qs.exclude(ends_at__isnull=True).order_by('-ends_at').values_list('ends_at', flat=True).first()
    candidates = [value for value in (
        getattr(subscription, 'paid_until', None), entitlement_end,
    ) if value and value > now]
    return max(candidates) if candidates else now


@transaction.atomic
def claim_gift(*, code: str, user) -> tuple[SubscriptionGift, ClubEntitlement]:
    normalized = (code or '').strip().upper()
    gift = SubscriptionGift.objects.select_for_update().filter(code_hash=_code_hash(normalized)).first()
    if gift is None:
        raise ValueError('Код подарка не найден')
    if gift.status == SubscriptionGift.STATUS_CREATED and gift.provider:
        raise ValueError('Этот подарок ещё не оплачен')
    if gift.status not in (SubscriptionGift.STATUS_CREATED, SubscriptionGift.STATUS_PAID):
        raise ValueError('Этот подарок уже использован или отозван')
    if gift.expires_at and gift.expires_at <= timezone.now():
        gift.status = SubscriptionGift.STATUS_EXPIRED
        gift.save(update_fields=['status'])
        raise ValueError('Срок действия этого подарка истёк')

    profile = Profile.objects.filter(user=user).first()
    if profile is None:
        raise ValueError('Сначала создайте профиль Inter Oves')
    if (
        gift.recipient_telegram_user_id is not None
        and profile.telegram_user_id != gift.recipient_telegram_user_id
    ):
        raise ValueError('Этот подарок предназначен другому Telegram-аккаунту')

    now = timezone.now()
    starts_at = _current_access_end(user, now)
    ends_at = None if gift.is_forever else add_calendar_months(starts_at, gift.duration_months)
    entitlement = ClubEntitlement.objects.create(
        user=user,
        kind=ClubEntitlement.KIND_GIFT,
        starts_at=starts_at,
        ends_at=ends_at,
        gift=gift,
        created_by=gift.created_by,
    )
    subscription, _ = ClubSubscription.objects.select_for_update().get_or_create(
        user=user,
        defaults={
            'provider': ClubSubscription.PROVIDER_TRIBUTE,
            'status': ClubSubscription.STATUS_ACTIVE,
            'auto_renew': False,
        },
    )
    entitlement.club_subscription = subscription
    entitlement.save(update_fields=['club_subscription'])
    if ends_at is None or subscription.paid_until is None or subscription.paid_until < ends_at:
        subscription.paid_until = ends_at
    subscription.status = ClubSubscription.STATUS_CANCELLED
    subscription.auto_renew = False
    subscription.save(update_fields=['paid_until', 'status', 'auto_renew', 'updated_at'])
    gift.status = SubscriptionGift.STATUS_CLAIMED
    gift.claimed_by = user
    gift.claimed_at = now
    gift.save(update_fields=['status', 'claimed_by', 'claimed_at'])
    return gift, entitlement


@transaction.atomic
def revoke_gift_access(gift: SubscriptionGift, *, now=None) -> None:
    """Revoke a paid gift and any access it granted, without touching other access."""
    now = now or timezone.now()
    gift = SubscriptionGift.objects.select_for_update().get(pk=gift.pk)
    gift.entitlements.filter(revoked_at__isnull=True).update(revoked_at=now)
    if gift.status != SubscriptionGift.STATUS_REVOKED:
        gift.status = SubscriptionGift.STATUS_REVOKED
        gift.revoked_at = now
        gift.save(update_fields=['status', 'revoked_at'])

    user_id = gift.claimed_by_id
    if not user_id:
        return
    subscription = ClubSubscription.objects.select_for_update().filter(user_id=user_id).first()
    if subscription is None:
        return
    active_ends = list(
        subscription.entitlements.filter(
            revoked_at__isnull=True, starts_at__lte=now,
        ).exclude(ends_at__isnull=True).values_list('ends_at', flat=True)
    )
    if active_ends:
        replacement = max(active_ends)
    else:
        replacement = now
    if subscription.paid_until and subscription.paid_until > now:
        # paid_until is also maintained by the billing providers. Only reduce
        # it when the revoked gift was the source of that visible end date.
        gift_end = gift.entitlements.order_by('-ends_at').values_list('ends_at', flat=True).first()
        if gift_end and subscription.paid_until <= gift_end:
            subscription.paid_until = replacement
            subscription.save(update_fields=['paid_until', 'updated_at'])


def gift_duration_label(gift: SubscriptionGift) -> str:
    return _gift_duration_label(gift)


def purchaser_gifts(user):
    expire_subscription_gifts(purchaser=user)
    gifts = SubscriptionGift.objects.filter(
        purchaser=user,
        status__in=(SubscriptionGift.STATUS_CREATED, SubscriptionGift.STATUS_PAID,
                    SubscriptionGift.STATUS_CLAIMED, SubscriptionGift.STATUS_REVOKED,
                    SubscriptionGift.STATUS_EXPIRED),
    ).select_related('claimed_by', 'payment').order_by('-created_at')
    return [
        {
            'gift': gift,
            'code': decrypt_gift_code(gift) if gift.status != SubscriptionGift.STATUS_CREATED else '',
            'duration_label': gift_duration_label(gift),
        }
        for gift in gifts
    ]


def expire_subscription_gifts(*, purchaser=None, now=None) -> int:
    """Mark unpaid/unclaimed gifts past their validity date as expired."""
    now = now or timezone.now()
    filters = {
        'status__in': (SubscriptionGift.STATUS_CREATED, SubscriptionGift.STATUS_PAID),
        'expires_at__isnull': False,
        'expires_at__lte': now,
    }
    if purchaser is not None:
        filters['purchaser'] = purchaser
    return SubscriptionGift.objects.filter(**filters).update(status=SubscriptionGift.STATUS_EXPIRED)
