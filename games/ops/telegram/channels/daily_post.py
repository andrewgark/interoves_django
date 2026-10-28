"""Shared Telegram channel poster for a daily game.

A game supplies today's edition, the caption, and the teaser image.
Scheduling, the 00:15 MSK window, admin failure notices, and the other
networks stay here. Alphabetty has no poster: add a ``ChannelPostSpec``,
set ``channel_post_key`` on its registry row, and append the key to
``CHANNEL_TICK_ORDER``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.daily.page.section import MOSCOW
from games.ops.social.models import SocialQueuePost
from games.ops.social.publish import (
    claim_telegram_post,
    complete_telegram_publish,
    publish_instagram,
    publish_telegram,
    publish_threads,
    publish_twitter,
    queue_network,
    update_claimed_telegram_post,
)
from games.ops.telegram.images.errors import (
    admin_post_failure_message,
    admin_render_failure_message,
    describe_post_failure,
    describe_render_failure,
)
from games.ops.telegram.transport.config import (
    admin_chat_id,
    telegram_admin_configured,
    telegram_channel_configured,
)
from games.ops.telegram.transport.mtproto import telegram_user_configured

logger = logging.getLogger('application')

# Tick order is the historical cron order, which is not the registry order.
CHANNEL_TICK_ORDER = ('ladder', 'salad', 'censorly')


@dataclass(frozen=True)
class ChannelPostSpec:
    """One daily game's channel post, minus the shared scheduler."""

    key: str
    source: str
    stats_key: str
    prepare_hour: int
    prepare_minute: int
    window_minutes: int
    sync_task: bool
    warn_when_late: bool
    resolve_today: Callable
    edition_date: Callable
    build_caption: Callable
    render: Callable
    render_failure_url: Callable
    publish_at: Callable
    send_photo: Callable
    send_message: Callable
    skip_unconfigured_message: str
    skip_unpublished_message: str
    skip_unpublished_level: str
    failure_label: str
    render_failure_kind: str
    late_error: str
    scheduled_when: str
    admin_channel_preview: str
    image_filename: str
    render_failed_log: str
    prepare_failed_log: str
    notify_render_failure_log: str
    notify_late_log: str
    notify_post_failure_log: str
    notify_telegram_failure_log: str
    admin_preview_failed_log: str
    tick_failure_message: str
    missing_today_message: str
    preview_success: str
    preview_render_failed_log: str

    def process_tick(self, now: datetime | None = None) -> dict[str, int]:
        return process_channel_tick(self, now=now)


CHANNEL_POSTS: dict[str, ChannelPostSpec] = {}


def register_channel_post(spec: ChannelPostSpec) -> None:
    CHANNEL_POSTS[spec.key] = spec


def load_channel_post(key: str) -> ChannelPostSpec:
    """Import ``channels/<key>.py`` if that poster has not registered yet."""
    if key not in CHANNEL_POSTS:
        __import__('games.ops.telegram.channels.{}'.format(key))
    spec = CHANNEL_POSTS.get(key)
    if spec is None:
        raise KeyError(key)
    return spec


def iter_channel_posts() -> tuple[ChannelPostSpec, ...]:
    """Import the game posters and return them in cron order."""
    from games.daily.registry import DAILY_GAME_REGISTRY

    declared = {
        definition.channel_post_key
        for definition in DAILY_GAME_REGISTRY.all()
        if definition.channel_post_key
    }
    if declared != set(CHANNEL_TICK_ORDER):
        raise ValueError(
            'daily channel posts {} do not match the tick order {}'.format(
                sorted(declared),
                CHANNEL_TICK_ORDER,
            )
        )
    return tuple(load_channel_post(key) for key in CHANNEL_TICK_ORDER)


def moscow_now(now: datetime | None = None) -> datetime:
    now = now or timezone.now()
    return now.astimezone(MOSCOW)


def publish_clock(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MOSCOW)


def channel_ready() -> bool:
    return telegram_user_configured() and telegram_channel_configured()


def _in_window(msk: datetime, spec: ChannelPostSpec) -> bool:
    start = msk.replace(
        hour=spec.prepare_hour,
        minute=spec.prepare_minute,
        second=0,
        microsecond=0,
    )
    return start <= msk < start + timedelta(minutes=spec.window_minutes)


def _queue_other_networks(post: SocialQueuePost, run_at: datetime, *, force: bool = False) -> None:
    post.refresh_from_db()
    for network in ('twitter', 'instagram', 'threads'):
        status = getattr(post, '{}_status'.format(network))
        queued_for = getattr(post, '{}_queued_for'.format(network))
        if status in (SocialQueuePost.STATUS_SENT, SocialQueuePost.STATUS_SKIPPED) and not force:
            continue
        if force or status != SocialQueuePost.STATUS_QUEUED or not queued_for:
            queue_network(post, network, run_at)
            post.refresh_from_db()


def _finish_other_networks(spec: ChannelPostSpec, post: SocialQueuePost, *, force: bool = False) -> None:
    if not post.telegram_ok:
        return
    run_at = post.telegram_scheduled_for or (
        spec.publish_at(post.ladder_date) if post.ladder_date else None
    )
    if run_at is None:
        return
    _queue_other_networks(post, run_at, force=force)


def _notify_admin(spec: ChannelPostSpec, send_text: str, log_message: str) -> None:
    if not telegram_admin_configured():
        return
    try:
        spec.send_message(admin_chat_id(), send_text)
    except Exception:
        logger.exception(log_message)


def _mark_failed(spec: ChannelPostSpec, post, claim_token: str, *, stage: str, exc: BaseException):
    error = describe_post_failure(spec.failure_label.format(post.ladder_number), stage, exc)
    complete_telegram_publish(
        post.pk,
        claim_token,
        status=SocialQueuePost.STATUS_FAILED,
        error=error,
    )
    _notify_admin(spec, admin_post_failure_message(error), spec.notify_post_failure_log)
    post.refresh_from_db()
    return post


def schedule_channel_post(
    spec: ChannelPostSpec,
    *,
    now: datetime | None = None,
    force: bool = False,
    immediate: bool = False,
    notify_admin: bool = True,
) -> SocialQueuePost | None:
    """Render today's teaser and hand Telegram a scheduled channel post."""
    if not channel_ready():
        logger.debug(spec.skip_unconfigured_message)
        return None

    now = now or timezone.now()
    edition = spec.resolve_today(now)
    if edition is None:
        getattr(logger, spec.skip_unpublished_level)(spec.skip_unpublished_message)
        return None

    edition_date = spec.edition_date(edition)
    post = SocialQueuePost.objects.filter(
        source=spec.source,
        ladder_date=edition_date,
    ).first()
    if post is None:
        try:
            with transaction.atomic():
                post = SocialQueuePost.objects.create(
                    source=spec.source,
                    ladder_date=edition_date,
                    ladder_number=edition.number,
                    task=edition.task,
                    play_url=edition.play_url,
                    caption='',
                    telegram_status=SocialQueuePost.STATUS_PENDING,
                )
        except IntegrityError:
            post = SocialQueuePost.objects.filter(
                source=spec.source,
                ladder_date=edition_date,
            ).first()
    if post is None:
        return None

    if spec.sync_task and post.task_id != edition.task.pk:
        post.task = edition.task
        post.save(update_fields=['task', 'updated_at'])
    if post.telegram_ok and not force:
        _finish_other_networks(spec, post, force=False)
        return post

    claim_token = claim_telegram_post(post.pk, force=force)
    if claim_token is None:
        post.refresh_from_db()
        if post.telegram_ok and not force:
            _finish_other_networks(spec, post, force=False)
        return post

    try:
        image_png = spec.render(edition)
        caption = spec.build_caption(edition)
    except Exception as exc:
        logger.exception(spec.render_failed_log, edition.number)
        error = describe_render_failure(
            spec.render_failure_kind.format(edition.number),
            spec.render_failure_url(edition),
            exc,
        )
        complete_telegram_publish(
            post.pk,
            claim_token,
            status=SocialQueuePost.STATUS_FAILED,
            error=error,
            count_attempt=True,
        )
        _notify_admin(spec, admin_render_failure_message(error), spec.notify_render_failure_log)
        post.refresh_from_db()
        return post

    post.ladder_number = edition.number
    post.play_url = edition.play_url
    post.caption = caption
    try:
        post.set_image_bytes(image_png, filename=spec.image_filename.format(edition.number))
        if not update_claimed_telegram_post(
            post.pk,
            claim_token,
            ladder_number=edition.number,
            play_url=edition.play_url,
            caption=caption,
            image=post.image.name,
        ):
            post.refresh_from_db()
            return post
        post.refresh_from_db()

        schedule_at = None if immediate else spec.publish_at(edition_date)
        if schedule_at is not None and schedule_at <= moscow_now(now) + timedelta(seconds=10):
            error = spec.late_error.format(edition_date)
            if spec.warn_when_late:
                logger.warning(error)
            complete_telegram_publish(
                post.pk,
                claim_token,
                status=SocialQueuePost.STATUS_FAILED,
                error=error,
                scheduled_for=schedule_at,
            )
            _notify_admin(spec, admin_post_failure_message(error), spec.notify_late_log)
            post.refresh_from_db()
            return post

        publish_telegram(
            post,
            immediate=immediate,
            schedule_at=schedule_at,
            force=force,
            claim_token=claim_token,
        )
    except Exception as exc:
        logger.exception(spec.prepare_failed_log, edition.number)
        return _mark_failed(spec, post, claim_token, stage='prepare_or_publish', exc=exc)

    completed_by_caller = bool(getattr(post, '_telegram_completion_applied', False))
    post.refresh_from_db()

    if post.telegram_status == SocialQueuePost.STATUS_FAILED:
        error = post.telegram_error or 'Telegram publish failed without details'
        _notify_admin(spec, admin_post_failure_message(error), spec.notify_telegram_failure_log)
        return post

    if completed_by_caller and notify_admin and telegram_admin_configured() and post.telegram_ok:
        when = (
            'сразу'
            if post.telegram_status == SocialQueuePost.STATUS_SENT
            else spec.scheduled_when
        )
        preview = spec.admin_channel_preview.format(post.ladder_number, when, caption)
        try:
            spec.send_photo(
                admin_chat_id(),
                post.image_bytes(),
                caption=preview,
                filename=spec.image_filename.format(post.ladder_number),
            )
        except Exception:
            logger.exception(spec.admin_preview_failed_log)

    if post.telegram_ok:
        if immediate:
            publish_twitter(post, force=force)
            publish_instagram(post, force=force)
            publish_threads(post, force=force)
        else:
            run_at = schedule_at or spec.publish_at(edition_date)
            _queue_other_networks(post, run_at, force=force)
        post.refresh_from_db()
    return post


def process_channel_tick(spec: ChannelPostSpec, now: datetime | None = None) -> dict[str, int]:
    """Minute cron: inside the prepare window, schedule today's post."""
    msk = moscow_now(now)
    stats = {'scheduled': 0, 'skipped': 1}
    if not _in_window(msk, spec):
        return stats

    before = SocialQueuePost.objects.filter(
        source=spec.source,
        ladder_date=msk.date(),
        telegram_status__in=(
            SocialQueuePost.STATUS_SCHEDULED,
            SocialQueuePost.STATUS_SENT,
        ),
    ).exists()
    post = schedule_channel_post(spec, now=now, force=False, notify_admin=True)
    if post and post.telegram_ok and not before:
        stats['scheduled'] = 1
        stats['skipped'] = 0
    return stats


def preview_channel_post(spec: ChannelPostSpec, *, now: datetime | None = None) -> tuple[bool, str]:
    """Render today's teaser and send it to the admin bot chat only."""
    if not telegram_admin_configured():
        return False, 'TELEGRAM_ADMIN_CHAT_ID / bot token not configured'
    edition = spec.resolve_today(now)
    if edition is None:
        return False, spec.missing_today_message
    try:
        image_png = spec.render(edition)
        caption = spec.build_caption(edition)
    except Exception as exc:
        logger.exception(spec.preview_render_failed_log)
        return False, 'Ошибка рендера: {}'.format(exc)
    return deliver_admin_photo(
        spec.send_photo,
        image_png=image_png,
        caption=caption,
        filename=spec.image_filename.format(edition.number),
        success_message=spec.preview_success.format(
            number=edition.number,
            message_id='{message_id}',
        ),
    )


def deliver_admin_photo(
    send_photo,
    *,
    image_png,
    caption: str,
    filename: str,
    success_message: str,
) -> tuple[bool, str]:
    result = send_photo(
        admin_chat_id(),
        image_png,
        caption=caption,
        filename=filename,
    )
    if result is None:
        return False, 'Не удалось отправить фото в admin-чат'
    return True, success_message.format(message_id=result.get('message_id'))
