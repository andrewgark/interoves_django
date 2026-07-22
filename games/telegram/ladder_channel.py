"""Daily ladder post for the public Telegram channel via SocialQueuePost."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.ladder_daily import (
    LADDER_GAME_ID,
    MOSCOW,
    current_ladder_number,
    is_ladder_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.social.models import SocialQueuePost
from games.social.publish import publish_telegram, queue_network
from games.telegram.api import send_photo
from games.telegram.config import (
    admin_chat_id,
    telegram_admin_configured,
    telegram_channel_configured,
)
from games.telegram.game_urls import admin_url
from games.telegram.ladder_image import render_ladder_teaser_png
from games.telegram.mtproto import telegram_user_configured

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
    play_url = admin_url('/games/ladder/{}/'.format(number))
    return TodayLadder(
        game=game,
        number=number,
        link=link,
        task=task,
        play_url=play_url,
        ladder_date=msk.date(),
    )


def resolve_ladder_by_number(number: int, now: datetime | None = None) -> TodayLadder | None:
    """Like resolve_today_ladder but for a specific (published) ladder number."""
    now = now or timezone.now()
    msk = moscow_now(now)
    game = Game.objects.filter(id=LADDER_GAME_ID).first()
    if game is None:
        return None
    if not is_ladder_number_published(game, number, now):
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
    play_url = admin_url('/games/ladder/{}/'.format(number))
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
    """Render today's ladder teaser and send it to the admin bot chat only."""
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


def _is_preparing(post: SocialQueuePost) -> bool:
    return (
        post.telegram_status == SocialQueuePost.STATUS_PENDING
        and post.telegram_error == 'preparing'
    )


def _queue_x_ig_for_ladder(
    post: SocialQueuePost,
    run_at: datetime,
    *,
    force: bool = False,
) -> None:
    """Put X/IG on the internal schedule for run_at (usually 16:30 MSK)."""
    post.refresh_from_db()
    if force or post.twitter_status not in (
        SocialQueuePost.STATUS_SENT,
        SocialQueuePost.STATUS_SKIPPED,
    ):
        if force or post.twitter_status != SocialQueuePost.STATUS_QUEUED or not post.twitter_queued_for:
            queue_network(post, 'twitter', run_at)
            post.refresh_from_db()
    if force or post.instagram_status not in (
        SocialQueuePost.STATUS_SENT,
        SocialQueuePost.STATUS_SKIPPED,
    ):
        if (
            force
            or post.instagram_status != SocialQueuePost.STATUS_QUEUED
            or not post.instagram_queued_for
        ):
            queue_network(post, 'instagram', run_at)


def _maybe_finish_other_networks(post: SocialQueuePost, *, force: bool = False) -> None:
    """Ensure X/IG are queued for 16:30 when TG is already ok (idempotent morning retry)."""
    if not post.telegram_ok:
        return
    run_at = post.telegram_scheduled_for or (
        publish_at_for_date(post.ladder_date) if post.ladder_date else None
    )
    if run_at is None:
        return
    post.refresh_from_db()
    _queue_x_ig_for_ladder(post, run_at, force=force)


def schedule_ladder_channel_post(
    *,
    now: datetime | None = None,
    force: bool = False,
    immediate: bool = False,
    notify_admin: bool = True,
) -> SocialQueuePost | None:
    """
    At ~00:15 MSK: render today's ladder into a SocialQueuePost, put Telegram into
    native deferred for 16:30 MSK, and queue X/IG internally for the same time.
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

    existing = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_LADDER,
        ladder_date=ladder.ladder_date,
    ).first()
    if existing and existing.telegram_ok and not force:
        _maybe_finish_other_networks(existing, force=False)
        return existing
    if (
        existing
        and not force
        and _is_preparing(existing)
        and (timezone.now() - existing.created_at) < timedelta(minutes=2)
    ):
        return existing

    if existing is None:
        try:
            with transaction.atomic():
                existing = SocialQueuePost.objects.create(
                    source=SocialQueuePost.SOURCE_LADDER,
                    ladder_date=ladder.ladder_date,
                    ladder_number=ladder.number,
                    play_url=ladder.play_url,
                    caption='',
                    telegram_status=SocialQueuePost.STATUS_PENDING,
                    telegram_error='preparing',
                )
        except IntegrityError:
            existing = SocialQueuePost.objects.filter(
                source=SocialQueuePost.SOURCE_LADDER,
                ladder_date=ladder.ladder_date,
            ).first()
            if existing and existing.telegram_ok and not force:
                _maybe_finish_other_networks(existing, force=False)
                return existing
            if existing and not force and _is_preparing(existing):
                return existing

    try:
        image_png = render_ladder_teaser_png(ladder.task, ladder_number=ladder.number)
        caption = build_caption(ladder)
    except Exception:
        logger.exception('Ladder channel render failed for №%s', ladder.number)
        if existing:
            existing.ladder_number = ladder.number
            existing.play_url = ladder.play_url
            existing.telegram_status = SocialQueuePost.STATUS_FAILED
            existing.telegram_error = 'render failed'
            existing.save(update_fields=[
                'ladder_number', 'play_url', 'telegram_status', 'telegram_error', 'updated_at',
            ])
            return existing
        return None

    existing.ladder_number = ladder.number
    existing.play_url = ladder.play_url
    existing.caption = caption
    existing.set_image_bytes(image_png, filename='ladder-{}.png'.format(ladder.number))
    existing.telegram_error = ''
    existing.save()

    schedule_at = None if immediate else publish_at_for_date(ladder.ladder_date)
    msk = moscow_now(now)
    if schedule_at is not None and schedule_at <= msk + timedelta(seconds=10):
        error = (
            '16:30 MSK already passed for {}; refusing to post immediately. '
            'Use --now only if you really want to publish now.'.format(ladder.ladder_date)
        )
        logger.warning(error)
        existing.telegram_status = SocialQueuePost.STATUS_FAILED
        existing.telegram_error = error
        existing.telegram_scheduled_for = schedule_at
        existing.save(update_fields=[
            'telegram_status', 'telegram_error', 'telegram_scheduled_for', 'updated_at',
        ])
        return existing

    publish_telegram(
        existing,
        immediate=immediate,
        schedule_at=schedule_at,
        force=force,
    )
    existing.refresh_from_db()

    if notify_admin and telegram_admin_configured() and existing.telegram_ok:
        when = (
            'сразу'
            if existing.telegram_status == SocialQueuePost.STATUS_SENT
            else 'в отложенные на 16:30 МСК'
        )
        preview = 'Канал @interoves: лесенка №{} — {}\n\n{}'.format(
            existing.ladder_number, when, caption,
        )
        try:
            send_photo(
                admin_chat_id(),
                existing.image_bytes(),
                caption=preview,
                filename='ladder-{}.png'.format(existing.ladder_number),
            )
        except Exception:
            logger.exception('Admin preview for ladder channel post failed')

    if existing.telegram_ok:
        if immediate:
            from games.social.publish import publish_instagram, publish_twitter

            publish_twitter(existing, force=force)
            publish_instagram(existing, force=force)
        else:
            run_at = schedule_at or publish_at_for_date(ladder.ladder_date)
            _queue_x_ig_for_ladder(existing, run_at, force=force)
        existing.refresh_from_db()
    return existing


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

    before = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_LADDER,
        ladder_date=msk.date(),
        telegram_status__in=(
            SocialQueuePost.STATUS_SCHEDULED,
            SocialQueuePost.STATUS_SENT,
        ),
    ).exists()
    post = schedule_ladder_channel_post(now=now, force=False, notify_admin=True)
    if post and post.telegram_ok and not before:
        stats['scheduled'] = 1
        stats['skipped'] = 0
    return stats
