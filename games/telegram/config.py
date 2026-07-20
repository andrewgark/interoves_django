from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


def parse_chat_id_list(raw: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.replace('\n', ',').split(',') if part.strip()]


def telegram_bot_configured() -> bool:
    return bool(getattr(settings, 'TELEGRAM_BOT_TOKEN', ''))


def telegram_admin_configured() -> bool:
    return telegram_bot_configured() and bool(getattr(settings, 'TELEGRAM_ADMIN_CHAT_ID', ''))


def telegram_announce_configured() -> bool:
    return telegram_bot_configured() and bool(getattr(settings, 'TELEGRAM_ANNOUNCE_CHAT_IDS', []))


def telegram_channel_configured() -> bool:
    return bool(getattr(settings, 'TELEGRAM_CHANNEL_CHAT_ID', ''))


def telegram_user_mtproto_configured() -> bool:
    return bool(
        getattr(settings, 'TELEGRAM_API_ID', 0)
        and getattr(settings, 'TELEGRAM_API_HASH', '')
        and getattr(settings, 'TELEGRAM_USER_SESSION', '')
    )


def admin_chat_id() -> str:
    return str(getattr(settings, 'TELEGRAM_ADMIN_CHAT_ID', '') or '')


def announce_chat_ids() -> list[str]:
    return [str(chat_id) for chat_id in getattr(settings, 'TELEGRAM_ANNOUNCE_CHAT_IDS', [])]


def channel_chat_id() -> str:
    return str(getattr(settings, 'TELEGRAM_CHANNEL_CHAT_ID', '') or '')

def is_admin_chat(chat_id) -> bool:
    configured = admin_chat_id()
    return bool(configured) and str(chat_id) == configured


def is_announce_chat(chat_id) -> bool:
    return str(chat_id) in announce_chat_ids()


def admin_mute_cache_key() -> str:
    return 'telegram:admin:mute_until'


def admin_is_muted() -> bool:
    until = cache.get(admin_mute_cache_key())
    if not until:
        return False
    if timezone.now().timestamp() >= float(until):
        cache.delete(admin_mute_cache_key())
        return False
    return True


def set_admin_mute(minutes: int) -> None:
    cache.set(admin_mute_cache_key(), timezone.now().timestamp() + minutes * 60, timeout=minutes * 60 + 60)


def clear_admin_mute() -> None:
    cache.delete(admin_mute_cache_key())


def game_telegram_announce_enabled(game) -> bool:
    tags = game.tags or {}
    return bool(tags.get('telegram_announce'))
