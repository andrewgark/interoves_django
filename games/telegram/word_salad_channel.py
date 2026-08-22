"""Daily salad post for the public Telegram channel via SocialQueuePost."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

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
from games.telegram.mtproto import telegram_user_configured
from games.telegram.word_salad_image import render_word_salad_teaser_png
from games.word_salad import WORD_SALAD_GAME_ID
from games.word_salad_daily import (
    MOSCOW,
    current_word_salad_number,
    is_word_salad_number_published,
)

logger = logging.getLogger('application')

PREPARE_HOUR = 0
PREPARE_MINUTE = 15
PUBLISH_HOUR = 14
PUBLISH_MINUTE = 30
WINDOW_MINUTES = 5


@dataclass
class TodaySalad:
    game: Game
    number: int
    link: GameTaskGroup
    task: Task
    play_url: str
    salad_date: date


def moscow_now(now: datetime | None = None) -> datetime:
    now = now or timezone.now()
    return now.astimezone(MOSCOW)


def publish_at_for_date(salad_date: date) -> datetime:
    """14:30 MSK on the salad's calendar day (two hours before the ladder teaser)."""
    return datetime.combine(salad_date, time(PUBLISH_HOUR, PUBLISH_MINUTE), tzinfo=MOSCOW)


def _escape(text) -> str:
    return html.escape(str(text), quote=False)


def _resolve_salad(number: int, now: datetime) -> TodaySalad | None:
    msk = moscow_now(now)
    game = Game.objects.filter(id=WORD_SALAD_GAME_ID).first()
    if game is None:
        return None
    if not is_word_salad_number_published(game, number, now):
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
        .filter(task_group_id=link.task_group_id, task_type='word_salad')
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
    play_url = admin_url('/word_salad/{}/'.format(number))
    return TodaySalad(
        game=game,
        number=number,
        link=link,
        task=task,
        play_url=play_url,
        salad_date=msk.date(),
    )


def resolve_today_salad(now: datetime | None = None) -> TodaySalad | None:
    now = now or timezone.now()
    game = Game.objects.filter(id=WORD_SALAD_GAME_ID).first()
    if game is None:
        return None
    number = current_word_salad_number(game, now)
    if number is None:
        return None
    return _resolve_salad(number, now)


def resolve_salad_by_number(number: int, now: datetime | None = None) -> TodaySalad | None:
    now = now or timezone.now()
    return _resolve_salad(number, now)


def build_caption(salad: TodaySalad) -> str:
    theme = (salad.task.text or '').strip()
    lines = [
        '🥗 <b>Салат №{}</b>'.format(_escape(salad.number)),
        '',
    ]
    if theme:
        lines.append(_escape(theme))
        lines.append('')
    lines.append(salad.play_url)
    return '\n'.join(lines)


def salad_channel_ready() -> bool:
    return telegram_user_configured() and telegram_channel_configured()


def preview_salad_to_admin(*, now: datetime | None = None) -> tuple[bool, str]:
    """Render today's salad teaser and send it to the admin bot chat only."""
    if not telegram_admin_configured():
        return False, 'TELEGRAM_ADMIN_CHAT_ID / bot token not configured'

    salad = resolve_today_salad(now)
    if salad is None:
        return False, 'Нет опубликованного салата на сегодня'

    try:
        image_png = render_word_salad_teaser_png(
            salad.task,
            salad_number=salad.number,
            fallback_to_pillow=False,
        )
        caption = build_caption(salad)
    except Exception as exc:
        logger.exception('Salad admin preview render failed')
        return False, 'Ошибка рендера: {}'.format(exc)

    result = send_photo(
        admin_chat_id(),
        image_png,
        caption=caption,
        filename='salad-{}.png'.format(salad.number),
    )
    if result is None:
        return False, 'Не удалось отправить фото в admin-чат'
    return True, 'Салат №{} → admin chat (message_id={})'.format(
        salad.number, result.get('message_id'),
    )


def _is_preparing(post: SocialQueuePost) -> bool:
    return (
        post.telegram_status == SocialQueuePost.STATUS_PENDING
        and post.telegram_error == 'preparing'
    )


def _queue_x_ig_for_salad(
    post: SocialQueuePost,
    run_at: datetime,
    *,
    force: bool = False,
) -> None:
    """Put X/IG on the internal schedule for run_at (usually 14:30 MSK)."""
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
    if not post.telegram_ok:
        return
    run_at = post.telegram_scheduled_for or (
        publish_at_for_date(post.ladder_date) if post.ladder_date else None
    )
    if run_at is None:
        return
    post.refresh_from_db()
    _queue_x_ig_for_salad(post, run_at, force=force)


def schedule_salad_channel_post(
    *,
    now: datetime | None = None,
    force: bool = False,
    immediate: bool = False,
    notify_admin: bool = True,
) -> SocialQueuePost | None:
    """
    At ~00:15 MSK: render today's salad into a SocialQueuePost, put Telegram into
    native deferred for 14:30 MSK, and queue X/IG internally for the same time.
    """
    if not salad_channel_ready():
        logger.debug(
            'Salad channel schedule skipped: need TELEGRAM_CHANNEL_CHAT_ID + user MTProto session'
        )
        return None

    salad = resolve_today_salad(now)
    if salad is None:
        logger.warning('Salad channel schedule skipped: no published salad for today')
        return None

    existing = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_WORD_SALAD,
        ladder_date=salad.salad_date,
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
                    source=SocialQueuePost.SOURCE_WORD_SALAD,
                    ladder_date=salad.salad_date,
                    ladder_number=salad.number,
                    play_url=salad.play_url,
                    caption='',
                    telegram_status=SocialQueuePost.STATUS_PENDING,
                    telegram_error='preparing',
                )
        except IntegrityError:
            existing = SocialQueuePost.objects.filter(
                source=SocialQueuePost.SOURCE_WORD_SALAD,
                ladder_date=salad.salad_date,
            ).first()
            if existing and existing.telegram_ok and not force:
                _maybe_finish_other_networks(existing, force=False)
                return existing
            if existing and not force and _is_preparing(existing):
                return existing

    try:
        image_png = render_word_salad_teaser_png(
            salad.task,
            salad_number=salad.number,
            fallback_to_pillow=False,
        )
        caption = build_caption(salad)
    except Exception:
        logger.exception('Salad channel render failed for №%s', salad.number)
        if existing:
            existing.ladder_number = salad.number
            existing.play_url = salad.play_url
            existing.telegram_status = SocialQueuePost.STATUS_FAILED
            existing.telegram_error = 'render failed'
            existing.save(update_fields=[
                'ladder_number', 'play_url', 'telegram_status', 'telegram_error', 'updated_at',
            ])
            return existing
        return None

    existing.ladder_number = salad.number
    existing.play_url = salad.play_url
    existing.caption = caption
    existing.set_image_bytes(image_png, filename='salad-{}.png'.format(salad.number))
    existing.telegram_error = ''
    existing.save()

    schedule_at = None if immediate else publish_at_for_date(salad.salad_date)
    msk = moscow_now(now)
    if schedule_at is not None and schedule_at <= msk + timedelta(seconds=10):
        error = (
            '14:30 MSK already passed for {}; refusing to post immediately. '
            'Use --now only if you really want to publish now.'.format(salad.salad_date)
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
            else 'в отложенные на 14:30 МСК'
        )
        preview = 'Канал @interoves: салат №{} — {}\n\n{}'.format(
            existing.ladder_number, when, caption,
        )
        try:
            send_photo(
                admin_chat_id(),
                existing.image_bytes(),
                caption=preview,
                filename='salad-{}.png'.format(existing.ladder_number),
            )
        except Exception:
            logger.exception('Admin preview for salad channel post failed')

    if existing.telegram_ok:
        if immediate:
            from games.social.publish import publish_instagram, publish_twitter

            publish_twitter(existing, force=force)
            publish_instagram(existing, force=force)
        else:
            run_at = schedule_at or publish_at_for_date(salad.salad_date)
            _queue_x_ig_for_salad(existing, run_at, force=force)
        existing.refresh_from_db()
    return existing


def prepare_salad_channel_post(**kwargs):
    return schedule_salad_channel_post(**kwargs)


def publish_salad_channel_post(*, force: bool = False, **kwargs):
    """Immediate publish (no schedule) — for --now / manual catch-up."""
    return schedule_salad_channel_post(force=force, immediate=True, **kwargs)


def _in_window(msk: datetime, hour: int, minute: int) -> bool:
    start = msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end = start + timedelta(minutes=WINDOW_MINUTES)
    return start <= msk < end


def process_salad_channel_tick(now: datetime | None = None) -> dict[str, Any]:
    """
    Minute cron: at 00:15 MSK schedule today's salad for 14:30 MSK via MTProto.
    Telegram itself publishes at schedule_date — no 14:30 job needed for TG.
    """
    msk = moscow_now(now)
    stats = {'scheduled': 0, 'skipped': 1}

    if not _in_window(msk, PREPARE_HOUR, PREPARE_MINUTE):
        return stats

    before = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_WORD_SALAD,
        ladder_date=msk.date(),
        telegram_status__in=(
            SocialQueuePost.STATUS_SCHEDULED,
            SocialQueuePost.STATUS_SENT,
        ),
    ).exists()
    post = schedule_salad_channel_post(now=now, force=False, notify_admin=True)
    if post and post.telegram_ok and not before:
        stats['scheduled'] = 1
        stats['skipped'] = 0
    return stats
