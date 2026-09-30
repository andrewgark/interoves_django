"""Creation and redemption of one-time Club subscription gifts."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from django.db import transaction
from django.utils import timezone

from games.club_yookassa import add_calendar_months
from games.models import ClubEntitlement, ClubSubscription, Profile, SubscriptionGift

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode('ascii')).hexdigest()


def _new_code() -> str:
    raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return 'IO-{}-{}'.format(raw[:4], raw[4:])


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
            )
            return CreatedGift(gift=gift, code=code)
        except Exception:
            if not SubscriptionGift.objects.filter(code_hash=_code_hash(code)).exists():
                raise
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
    if gift.status != SubscriptionGift.STATUS_CREATED:
        raise ValueError('Этот подарок уже использован или отозван')

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
    if subscription.paid_until is None or subscription.paid_until < ends_at if ends_at else True:
        subscription.paid_until = ends_at
    subscription.status = ClubSubscription.STATUS_CANCELLED
    subscription.auto_renew = False
    subscription.save(update_fields=['paid_until', 'status', 'auto_renew', 'updated_at'])
    gift.status = SubscriptionGift.STATUS_CLAIMED
    gift.claimed_by = user
    gift.claimed_at = now
    gift.save(update_fields=['status', 'claimed_by', 'claimed_at'])
    return gift, entitlement


def gift_duration_label(gift: SubscriptionGift) -> str:
    return _gift_duration_label(gift)
