"""Daily ladder post for the public Telegram channel via MTProto schedule_date."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from django.utils import timezone

from games.ladder_daily import (
    LADDER_GAME_ID,
    MOSCOW,
    current_ladder_number,
    is_ladder_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.telegram.api import send_photo
from games.telegram.config import (
    admin_chat_id,
    channel_chat_id,
    telegram_admin_configured,
    telegram_channel_configured,
)
from games.telegram.game_urls import admin_url, task_group_play_path
from games.telegram.ladder_image import render_ladder_teaser_png
from games.telegram.models import TelegramLadderChannelPost
from games.telegram.mtproto import schedule_channel_photo_sync, telegram_user_configured

logger = logging.getLogger('application')

PREPARE_HOUR = 0
PREPARE_MINUTE = 15
PUBLISH_HOUR = 16
PUBLISH_MINUTE = 30
WINDOW_MINUTES = 5


@dataclass
class TodayLadder:
    game: Game
    number: int
    link: GameTaskGroup
    task: Task
    play_url: str
    ladder_date: date


def moscow_now(now: datetime | None = None) -> datetime:
    now = now or timezone.now()
    return now.astimezone(MOSCOW)


def publish_at_for_date(ladder_date: date) -> datetime:
    """16:30 MSK on the ladder's calendar day."""
    return datetime.combine(ladder_date, time(PUBLISH_HOUR, PUBLISH_MINUTE), tzinfo=MOSCOW)


def _escape(text) -> str:
    return html.escape(str(text), quote=False)


def resolve_today_ladder(now: datetime | None = None) -> TodayLadder | None:
    now = now or timezone.now()
    msk = moscow_now(now)
    game = Game.objects.filter(id=LADDER_GAME_ID).first()
    if game is None:
        return None
    number = current_ladder_number(game, now)
    if number is None or not is_ladder_number_published(game, number, now):
        return None
    link = (
        GameTaskGroup.objects
        .filter(game=game, number=str(number))
        .select_related('task_group')
        .first()
    )
    if link is None or link.task_group_id is None:
        return None
    task = (
        Task.objects
        .filter(task_group_id=link.task_group_id, task_type='raddle')
        .order_by('id')
        .first()
    )
    if task is None:
        task = (
            Task.objects
            .filter(task_group_id=link.task_group_id)
            .order_by('id')
            .first()
        )
    if task is None:
        return None
    play_url = admin_url(task_group_play_path(game, number))
    return TodayLadder(
        game=game,
        number=number,
        link=link,
        task=task,
        play_url=play_url,
        ladder_date=msk.date(),
    )


def build_caption(ladder: TodayLadder, *, parsed=None) -> str:
    from games.raddle import parse_raddle_data

    data = parsed or parse_raddle_data(ladder.task)
    words = data.get('words') or []
    title_from = words[0] if words else ''
    title_to = words[-1] if words else ''
    lines = [
        '🪜 <b>Лесенка №{}</b>'.format(_escape(ladder.number)),
        '',
        'От <b>{}</b> до <b>{}</b>'.format(_escape(title_from), _escape(title_to)),
        '',
        ladder.play_url,
    ]
    author = (ladder.task.tags or {}).get('author')
    if author:
        lines.insert(3, 'Автор: {}'.format(_escape(author)))
    return '\n'.join(lines)


def ladder_channel_ready() -> bool:
    return telegram_user_configured() and telegram_channel_configured()


def preview_ladder_to_admin(*, now: datetime | None = None) -> tuple[bool, str]:
    """
    Render today's ladder teaser and send it to the admin bot chat only
    (never to the public channel).
    """
    if not telegram_admin_configured():
        return False, 'TELEGRAM_ADMIN_CHAT_ID / bot token not configured'

    ladder = resolve_today_ladder(now)
    if ladder is None:
        return False, 'Нет опубликованной лесенки на сегодня'

    try:
        image_png = render_ladder_teaser_png(ladder.task, ladder_number=ladder.number)
        caption = build_caption(ladder)
    except Exception as exc:
        logger.exception('Ladder admin preview render failed')
        return False, 'Ошибка рендера: {}'.format(exc)

    result = send_photo(
        admin_chat_id(),
        image_png,
        caption=caption,
        filename='ladder-{}.png'.format(ladder.number),
    )
    if result is None:
        return False, 'Не удалось отправить фото в admin-чат'
    return True, 'Лесенка №{} → admin chat (message_id={})'.format(
        ladder.number, result.get('message_id'),
    )


def schedule_ladder_channel_post(
    *,
    now: datetime | None = None,
    force: bool = False,
    immediate: bool = False,
    notify_admin: bool = True,
) -> TelegramLadderChannelPost | None:
    """
    At ~00:15 MSK: render today's ladder and put a photo into the channel's
    Telegram scheduled queue for 16:30 MSK (MTProto schedule_date via Telethon).

    Requires a *user* session that can post to the channel — bots get
    SCHEDULE_BOT_NOT_ALLOWED even over MTProto.
    """
    if not ladder_channel_ready():
        logger.debug(
            'Ladder channel schedule skipped: need TELEGRAM_CHANNEL_CHAT_ID + user MTProto session'
        )
        return None

    ladder = resolve_today_ladder(now)
    if ladder is None:
        logger.warning('Ladder channel schedule skipped: no published ladder for today')
        return None

    existing = TelegramLadderChannelPost.objects.filter(ladder_date=ladder.ladder_date).first()
    if existing and existing.status in (
        TelegramLadderChannelPost.STATUS_SCHEDULED,
        TelegramLadderChannelPost.STATUS_SENT,
    ) and not force:
        return existing

    try:
        image_png = render_ladder_teaser_png(ladder.task, ladder_number=ladder.number)
        caption = build_caption(ladder)
    except Exception:
        logger.exception('Ladder channel render failed for №%s', ladder.number)
        if existing:
            existing.status = TelegramLadderChannelPost.STATUS_FAILED
            existing.error = 'render failed'
            existing.save(update_fields=['status', 'error'])
            return existing
        return None

    schedule_at = None if immediate else publish_at_for_date(ladder.ladder_date)
    msk = moscow_now(now)
    if schedule_at is not None and schedule_at <= msk + timedelta(seconds=10):
        # Never auto-publish: if 16:30 MSK already passed, skip (use --now only if intentional).
        error = (
            '16:30 MSK already passed for {}; refusing to post immediately. '
            'Use --now only if you really want to publish now.'.format(ladder.ladder_date)
        )
        logger.warning(error)
        if existing:
            existing.ladder_number = ladder.number
            existing.play_url = ladder.play_url
            existing.caption = caption
            existing.image_png = image_png
            existing.status = TelegramLadderChannelPost.STATUS_FAILED
            existing.error = error
            existing.scheduled_for = schedule_at
            existing.save()
            return existing
        return TelegramLadderChannelPost.objects.create(
            ladder_date=ladder.ladder_date,
            ladder_number=ladder.number,
            play_url=ladder.play_url,
            caption=caption,
            image_png=image_png,
            status=TelegramLadderChannelPost.STATUS_FAILED,
            error=error,
            scheduled_for=schedule_at,
        )

    try:
        result = schedule_channel_photo_sync(
            chat=channel_chat_id(),
            photo_bytes=image_png,
            caption=caption,
            schedule_at=schedule_at,
            filename='ladder-{}.png'.format(ladder.number),
        )
    except Exception as exc:
        logger.exception('Ladder channel MTProto schedule failed for №%s', ladder.number)
        error = str(exc)[:500]
        if existing:
            existing.ladder_number = ladder.number
            existing.play_url = ladder.play_url
            existing.caption = caption
            existing.image_png = image_png
            existing.status = TelegramLadderChannelPost.STATUS_FAILED
            existing.error = error
            existing.save()
            return existing
        return TelegramLadderChannelPost.objects.create(
            ladder_date=ladder.ladder_date,
            ladder_number=ladder.number,
            play_url=ladder.play_url,
            caption=caption,
            image_png=image_png,
            status=TelegramLadderChannelPost.STATUS_FAILED,
            error=error,
            scheduled_for=schedule_at,
        )

    status = (
        TelegramLadderChannelPost.STATUS_SENT
        if immediate or schedule_at is None
        else TelegramLadderChannelPost.STATUS_SCHEDULED
    )
    fields = {
        'ladder_number': ladder.number,
        'play_url': ladder.play_url,
        'caption': caption,
        'image_png': image_png,
        'status': status,
        'error': '',
        'telegram_message_id': result.get('message_id'),
        'scheduled_for': schedule_at,
        'sent_at': timezone.now() if status == TelegramLadderChannelPost.STATUS_SENT else None,
    }
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.save()
        post = existing
    else:
        post = TelegramLadderChannelPost.objects.create(
            ladder_date=ladder.ladder_date,
            **fields,
        )

    if notify_admin and telegram_admin_configured():
        when = (
            'сразу'
            if status == TelegramLadderChannelPost.STATUS_SENT
            else 'в отложенные на 16:30 МСК'
        )
        preview = 'Канал @interoves: лесенка №{} — {}\n\n{}'.format(
            post.ladder_number, when, caption,
        )
        try:
            send_photo(
                admin_chat_id(),
                bytes(post.image_png),
                caption=preview,
                filename='ladder-{}.png'.format(post.ladder_number),
            )
        except Exception:
            logger.exception('Admin preview for ladder channel post failed')
    return post


# Backward-compatible aliases used by the management command.
def prepare_ladder_channel_post(**kwargs):
    return schedule_ladder_channel_post(**kwargs)


def publish_ladder_channel_post(*, force: bool = False, **kwargs):
    """Immediate publish (no schedule) — for --now / manual catch-up."""
    return schedule_ladder_channel_post(force=force, immediate=True, **kwargs)


def _in_window(msk: datetime, hour: int, minute: int) -> bool:
    start = msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end = start + timedelta(minutes=WINDOW_MINUTES)
    return start <= msk < end


def process_ladder_channel_tick(now: datetime | None = None) -> dict[str, Any]:
    """
    Minute cron: at 00:15 MSK schedule today's ladder for 16:30 MSK via MTProto.
    Telegram itself publishes at schedule_date — no 16:30 job needed.
    """
    msk = moscow_now(now)
    stats = {'scheduled': 0, 'skipped': 1}

    if not _in_window(msk, PREPARE_HOUR, PREPARE_MINUTE):
        return stats

    before = TelegramLadderChannelPost.objects.filter(
        ladder_date=msk.date(),
        status__in=(
            TelegramLadderChannelPost.STATUS_SCHEDULED,
            TelegramLadderChannelPost.STATUS_SENT,
        ),
    ).exists()
    post = schedule_ladder_channel_post(now=now, force=False, notify_admin=True)
    if post and post.status in (
        TelegramLadderChannelPost.STATUS_SCHEDULED,
        TelegramLadderChannelPost.STATUS_SENT,
    ) and not before:
        stats['scheduled'] = 1
        stats['skipped'] = 0
    return stats
