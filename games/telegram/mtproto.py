"""MTProto (Telethon) user-session helpers for channel scheduled posts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from io import BytesIO
from typing import Any, Callable

from django.conf import settings

logger = logging.getLogger('application')


def telegram_user_configured() -> bool:
    return bool(
        getattr(settings, 'TELEGRAM_API_ID', 0)
        and getattr(settings, 'TELEGRAM_API_HASH', '')
        and getattr(settings, 'TELEGRAM_USER_SESSION', '')
    )


def _build_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(settings.TELEGRAM_API_ID)
    api_hash = settings.TELEGRAM_API_HASH
    session = StringSession(settings.TELEGRAM_USER_SESSION)
    return TelegramClient(session, api_id, api_hash)


def run_sync(coro_factory: Callable[[], Any]):
    """Run an async Telethon coroutine from sync Django code."""
    return asyncio.run(coro_factory())


async def schedule_channel_photo(
    *,
    chat: str,
    photo_bytes: bytes,
    caption: str,
    schedule_at: datetime | None,
    filename: str = 'ladder.png',
) -> dict[str, Any]:
    """
    Post a photo to a channel via user MTProto.

    schedule_at — aware datetime; if set, message lands in Telegram's scheduled queue
    (visible in the channel's «Отложенные»). Bots cannot do this; user session can.
    """
    from telethon.tl.types import MessageMediaPhoto

    if not telegram_user_configured():
        raise RuntimeError('TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_USER_SESSION not configured')

    client = _build_client()
    async with client:
        me = await client.get_me()
        if me is None or getattr(me, 'bot', False):
            raise RuntimeError('Session must be a user account (not a bot)')

        entity = await client.get_entity(chat)
        photo = BytesIO(photo_bytes)
        photo.name = filename
        message = await client.send_file(
            entity,
            file=photo,
            caption=caption or None,
            parse_mode='html',
            force_document=False,
            schedule=schedule_at,
        )
        if isinstance(message, list):
            message = message[0] if message else None
        if message is None:
            raise RuntimeError('send_file returned empty result')

        return {
            'message_id': message.id,
            'date': getattr(message, 'date', None),
            'scheduled': schedule_at is not None,
            'media': isinstance(getattr(message, 'media', None), MessageMediaPhoto),
            'user_id': me.id,
        }


def schedule_channel_photo_sync(**kwargs) -> dict[str, Any]:
    return run_sync(lambda: schedule_channel_photo(**kwargs))
