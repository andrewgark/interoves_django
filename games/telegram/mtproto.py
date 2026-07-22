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


async def delete_channel_messages(*, chat: str, message_ids: list[int]) -> int:
    """Delete channel messages (including items in the scheduled queue)."""
    if not message_ids:
        return 0
    if not telegram_user_configured():
        raise RuntimeError('TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_USER_SESSION not configured')

    client = _build_client()
    async with client:
        entity = await client.get_entity(chat)
        ok = await client.delete_messages(entity, message_ids)
        return int(ok or 0)


def delete_channel_messages_sync(*, chat: str, message_ids: list[int]) -> int:
    return run_sync(lambda: delete_channel_messages(chat=chat, message_ids=message_ids))


async def fetch_scheduled_message(*, chat: str, message_id: int) -> dict[str, Any] | None:
    """Read one message from the channel's scheduled («Отложенные») queue.

    Returns the current caption (as HTML, reconstructed from message entities so
    formatting/links survive) or None if the message is no longer scheduled
    (e.g. already published or deleted).
    """
    if not telegram_user_configured():
        raise RuntimeError('TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_USER_SESSION not configured')

    from telethon.extensions import html as tl_html
    from telethon.tl.functions.messages import GetScheduledMessagesRequest

    client = _build_client()
    async with client:
        entity = await client.get_entity(chat)
        result = await client(
            GetScheduledMessagesRequest(peer=entity, id=[int(message_id)])
        )
        messages = getattr(result, 'messages', None) or []
        message = None
        for candidate in messages:
            if getattr(candidate, 'id', None) == int(message_id):
                message = candidate
                break
        if message is None:
            return None

        raw_text = getattr(message, 'message', '') or ''
        entities = getattr(message, 'entities', None)
        try:
            caption_html = tl_html.unparse(raw_text, entities)
        except Exception:
            caption_html = raw_text

        return {
            'message_id': getattr(message, 'id', message_id),
            'caption': caption_html,
            'caption_plain': raw_text,
            'date': getattr(message, 'date', None),
        }


def fetch_scheduled_message_sync(*, chat: str, message_id: int) -> dict[str, Any] | None:
    return run_sync(lambda: fetch_scheduled_message(chat=chat, message_id=message_id))
