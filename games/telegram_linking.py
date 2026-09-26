"""Verified Telegram identity linking through the existing Inter Oves bot."""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from games.models import Profile, TelegramLinkToken


TOKEN_TTL_MINUTES = 15
_TELEGRAM_HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')


def verified_public_handle(profile) -> str:
    """Username shown publicly after Telegram has confirmed this profile."""
    if profile is None or not getattr(profile, 'telegram_verified', False):
        return ''
    raw = (
        getattr(profile, 'telegram_username', '')
        or getattr(profile, 'telegram_handle', '')
        or ''
    )
    return str(raw).strip().lstrip('@')[:64]


def offer_telegram_gaps(profile) -> list[str]:
    """Why this profile cannot publish an offer that needs a Telegram contact."""
    if not getattr(profile, 'telegram_verified', False):
        return ['telegram_unverified']
    handle = verified_public_handle(profile)
    if not handle:
        return ['telegram_handle']
    if not _TELEGRAM_HANDLE_RE.match(handle):
        return ['telegram_handle_invalid']
    return []


def offer_profile_error(missing) -> str:
    if 'telegram_handle_invalid' in missing:
        return (
            'У привязанного Telegram нет подходящего username '
            '(5–32 символа: латиница, цифры, _).'
        )
    if 'telegram_unverified' in missing or 'telegram_handle' in missing:
        return 'Привяжите Telegram через вход. Вписать ник вручную нельзя.'
    return 'Заполните имя и фамилию.'


def apply_verified_telegram_handle(profile, username: str) -> None:
    """Store the username Telegram itself reported. Never keep a typed nick."""
    handle = str(username or '').strip().lstrip('@')[:64]
    profile.telegram_username = handle
    profile.telegram_handle = handle


class TelegramLinkError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class TelegramLinkResult:
    user_id: int
    telegram_user_id: int
    telegram_username: str
    next_path: str = ''


def user_has_telegram_link(user) -> bool:
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.telegram_verified and profile.telegram_user_id)


def sanitize_telegram_link_next(raw: str) -> str:
    path = str(raw or '').strip()
    if not path.startswith('/') or path.startswith('//') or '\\' in path:
        return ''
    if path.startswith('/subscription') or path.startswith('/pay'):
        return path[:200]
    return ''


def _relink_hint(next_path: str) -> str:
    if str(next_path or '').startswith('/subscription'):
        return 'Создайте новую на interoves.com/subscription/.'
    return 'Создайте новую на interoves.com/pay/.'


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def create_link_token(user, *, next_path: str = '') -> tuple[TelegramLinkToken, str]:
    now = timezone.now()
    # Invalidate older unused links for this account. Used rows remain as an audit trail.
    TelegramLinkToken.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    raw_token = secrets.token_urlsafe(24)
    row = TelegramLinkToken.objects.create(
        user=user,
        token_hash=_token_hash(raw_token),
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
        next_path=sanitize_telegram_link_next(next_path),
    )
    return row, raw_token


def telegram_deep_link(raw_token: str) -> str:
    username = str(getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or '').strip().lstrip('@')
    if not username:
        raise TelegramLinkError('bot_not_configured', 'Telegram-бот пока не настроен.')
    return 'https://t.me/{}?start={}'.format(username, raw_token)


def consume_link_token(raw_token: str, *, telegram_user_id, telegram_username='') -> TelegramLinkResult:
    raw_token = str(raw_token or '').strip()
    try:
        numeric_id = int(telegram_user_id)
        if numeric_id <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise TelegramLinkError('invalid_telegram_id', 'Telegram не передал корректный ID аккаунта.') from exc

    username = str(telegram_username or '').strip().lstrip('@')[:64]
    now = timezone.now()
    with transaction.atomic():
        token = (
            TelegramLinkToken.objects.select_for_update()
            .select_related('user')
            .filter(token_hash=_token_hash(raw_token))
            .first()
        )
        if token is None:
            raise TelegramLinkError('invalid_token', 'Ссылка недействительна. Создайте новую на сайте Inter Oves.')
        if token.used_at is not None:
            raise TelegramLinkError(
                'used_token',
                'Эта ссылка уже использована. {}'.format(_relink_hint(token.next_path)),
            )
        if token.expires_at <= now:
            raise TelegramLinkError(
                'expired_token',
                'Ссылка истекла. {}'.format(_relink_hint(token.next_path)),
            )

        profile = Profile.objects.select_for_update().filter(user=token.user).first()
        if profile is None:
            raise TelegramLinkError('profile_missing', 'Профиль Inter Oves не найден.')
        owner = (
            Profile.objects.select_for_update()
            .filter(telegram_user_id=numeric_id, telegram_verified=True)
            .exclude(pk=profile.pk)
            .first()
        )
        if owner is not None:
            raise TelegramLinkError(
                'identity_in_use',
                'Этот Telegram уже связан с другим аккаунтом Inter Oves. Напишите Андрею в Telegram: https://t.me/andrewgark',
            )

        profile.telegram_user_id = numeric_id
        profile.telegram_verified = True
        profile.telegram_linked_at = now
        apply_verified_telegram_handle(profile, username)
        profile.save(update_fields=[
            'telegram_user_id', 'telegram_username', 'telegram_handle',
            'telegram_verified', 'telegram_linked_at',
        ])
        token.used_at = now
        token.save(update_fields=['used_at'])

    from games.club_service import apply_pending_club_events_for_telegram

    apply_pending_club_events_for_telegram(numeric_id)

    return TelegramLinkResult(
        token.user_id,
        numeric_id,
        username,
        next_path=sanitize_telegram_link_next(token.next_path) or '/pay/?telegram=linked',
    )

