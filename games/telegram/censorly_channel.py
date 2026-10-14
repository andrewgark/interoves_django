"""Daily Censorly social post through SocialQueuePost."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from games.censorly_daily import (
    CENSORLY_GAME_ID,
    MOSCOW,
    current_censorly_number,
    is_censorly_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.section_paths import section_play_path
from games.social.models import SocialQueuePost
from games.social.publish import (
    claim_telegram_post,
    complete_telegram_publish,
    publish_telegram,
    queue_network,
    update_claimed_telegram_post,
)
from games.telegram.api import send_message, send_photo
from games.telegram.censorly_image import render_censorly_teaser_png
from games.telegram.config import (
    admin_chat_id,
    telegram_admin_configured,
    telegram_channel_configured,
)
from games.telegram.game_urls import admin_url
from games.telegram.mtproto import telegram_user_configured
from games.telegram.render_errors import (
    admin_post_failure_message,
    admin_render_failure_message,
    describe_post_failure,
    describe_render_failure,
)

logger = logging.getLogger('application')

PREPARE_HOUR = 0
PREPARE_MINUTE = 15
PUBLISH_HOUR = 18
PUBLISH_MINUTE = 30
WINDOW_MINUTES = 5


@dataclass
class TodayCensorly:
    game: Game
    number: int
    link: GameTaskGroup
    task: Task
    play_url: str
    censorly_date: date


def moscow_now(now: datetime | None = None) -> datetime:
    return (now or timezone.now()).astimezone(MOSCOW)


def publish_at_for_date(censorly_date: date) -> datetime:
    """18:30 MSK, two hours after the ladder's 16:30 MSK social post."""
    return datetime.combine(
        censorly_date, time(PUBLISH_HOUR, PUBLISH_MINUTE), tzinfo=MOSCOW,
    )


def _escape(text) -> str:
    return html.escape(str(text), quote=False)


def _trim_teaser_title(text: str, *, max_chars: int = 120) -> str:
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not text:
        return ''
    if len(text) > max_chars:
        text = text[:max_chars - 1].rsplit(' ', 1)[0].rstrip() + '…'
    return text


def teaser_title_for_task(task: Task) -> str:
    """Mask the words in the article title while preserving its punctuation."""
    from games.censorly.play import puzzle_from_task

    payload = puzzle_from_task(task)
    if not payload:
        return ''
    pieces = []
    for token in payload.get('title_tokens') or []:
        kind = token.get('kind')
        surface = str(token.get('surface') or '')
        if kind in ('content', 'stop'):
            try:
                length = int(token.get('length') or 0)
            except (TypeError, ValueError):
                length = 0
            pieces.append('█' * max(1, length))
        elif kind in ('heading', 'legacy_heading'):
            pieces.append(re.sub(r'[^\W_]+', lambda match: '█' * len(match.group(0)), surface))
        else:
            pieces.append(surface)
    return _trim_teaser_title(''.join(pieces))


def resolve_censorly_by_number(
    number: int,
    now: datetime | None = None,
    *,
    require_published: bool = True,
) -> TodayCensorly | None:
    now = now or timezone.now()
    msk = moscow_now(now)
    game = Game.objects.filter(id=CENSORLY_GAME_ID).first()
    if game is None:
        return None
    if require_published:
        tags = game.tags if isinstance(game.tags, dict) else {}
        if not tags.get('censorly_public'):
            return None
        if not is_censorly_number_published(game, number, now):
            return None
    if not str(number).isdigit():
        return None
    link = (
        GameTaskGroup.objects.filter(game=game, number=str(number))
        .select_related('task_group').first()
    )
    if link is None or link.is_deferred or link.task_group_id is None:
        return None
    task = (
        Task.objects.filter(task_group_id=link.task_group_id, task_type='censorly')
        .order_by('id').first()
    )
    if task is None:
        task = Task.objects.filter(task_group_id=link.task_group_id).order_by('id').first()
    if task is None:
        return None
    return TodayCensorly(
        game=game,
        number=int(number),
        link=link,
        task=task,
        play_url=admin_url(section_play_path(CENSORLY_GAME_ID, number)),
        censorly_date=msk.date(),
    )


def resolve_today_censorly(now: datetime | None = None) -> TodayCensorly | None:
    now = now or timezone.now()
    game = Game.objects.filter(id=CENSORLY_GAME_ID).first()
    if game is None:
        return None
    number = current_censorly_number(game, now)
    if number is None:
        return None
    return resolve_censorly_by_number(number, now)


def build_caption(
    censorly: TodayCensorly | int,
    *,
    play_url: str | None = None,
    title_mask: str | None = None,
) -> str:
    number = censorly.number if isinstance(censorly, TodayCensorly) else int(censorly)
    url = play_url or (
        censorly.play_url if isinstance(censorly, TodayCensorly)
        else admin_url(section_play_path(CENSORLY_GAME_ID, number))
    )
    if title_mask is None and isinstance(censorly, TodayCensorly):
        title_mask = teaser_title_for_task(censorly.task)
    title_mask = _trim_teaser_title(title_mask or '')
    if not title_mask:
        raise ValueError('Не удалось собрать скрытый заголовок статьи')
    return '\n'.join((
        '📰 <b>Цензурка №{}</b>'.format(_escape(number)),
        '',
        _escape(title_mask),
        '',
        url,
    ))


def censorly_channel_ready() -> bool:
    return telegram_user_configured() and telegram_channel_configured()


def preview_censorly_to_admin(
    *, now: datetime | None = None, number: int | None = None,
) -> tuple[bool, str]:
    """Send a rendered Censorly screenshot to the admin bot chat only."""
    if not telegram_admin_configured():
        return False, 'TELEGRAM_ADMIN_CHAT_ID / bot token not configured'
    censorly = (
        resolve_today_censorly(now) if number is None
        else resolve_censorly_by_number(number, now, require_published=False)
    )
    if number is None and censorly is None:
        return False, 'Нет опубликованной Цензурки на сегодня'
    target_number = censorly.number if censorly is not None else int(number)
    play_url = (
        censorly.play_url if censorly is not None
        else admin_url(section_play_path(CENSORLY_GAME_ID, target_number))
    )
    try:
        captured_title = []
        image_png = render_censorly_teaser_png(
            number=target_number, url=play_url, captured_title=captured_title,
        )
        caption = build_caption(
            target_number, play_url=play_url,
            title_mask=captured_title[0] if captured_title else '',
        )
    except Exception as exc:
        logger.exception('Censorly admin preview render failed')
        return False, 'Ошибка рендера: {}'.format(exc)
    result = send_photo(
        admin_chat_id(), image_png, caption=caption,
        filename='censorly-{}.png'.format(target_number),
    )
    if result is None:
        return False, 'Не удалось отправить фото в admin-чат'
    return True, 'Цензурка №{} → admin chat (message_id={})'.format(
        target_number, result.get('message_id'),
    )


def _queue_other_networks(post: SocialQueuePost, run_at: datetime, *, force=False) -> None:
    post.refresh_from_db()
    for network in ('twitter', 'instagram', 'threads'):
        status = getattr(post, '{}_status'.format(network))
        queued_for = getattr(post, '{}_queued_for'.format(network))
        if status in (SocialQueuePost.STATUS_SENT, SocialQueuePost.STATUS_SKIPPED) and not force:
            continue
        if force or status != SocialQueuePost.STATUS_QUEUED or not queued_for:
            queue_network(post, network, run_at)
            post.refresh_from_db()


def _mark_failed(post, claim_token, *, stage, exc):
    error = describe_post_failure('Цензурка №{}'.format(post.ladder_number), stage, exc)
    complete_telegram_publish(
        post.pk, claim_token, status=SocialQueuePost.STATUS_FAILED, error=error,
    )
    if telegram_admin_configured():
        try:
            send_message(admin_chat_id(), admin_post_failure_message(error))
        except Exception:
            logger.exception('Failed to notify admin about Censorly post failure')
    post.refresh_from_db()
    return post


def schedule_censorly_channel_post(
    *, now: datetime | None = None, force: bool = False,
    immediate: bool = False, notify_admin: bool = True,
) -> SocialQueuePost | None:
    """Render today's opening article, schedule Telegram for 18:30 MSK."""
    if not censorly_channel_ready():
        logger.debug('Censorly channel schedule skipped: Telegram channel is not configured')
        return None
    now = now or timezone.now()
    censorly = resolve_today_censorly(now)
    if censorly is None:
        logger.info('Censorly channel schedule skipped: no published daily task')
        return None

    post = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_CENSORLY,
        ladder_date=censorly.censorly_date,
    ).first()
    if post is None:
        try:
            with transaction.atomic():
                post = SocialQueuePost.objects.create(
                    source=SocialQueuePost.SOURCE_CENSORLY,
                    ladder_date=censorly.censorly_date,
                    ladder_number=censorly.number,
                    task=censorly.task,
                    play_url=censorly.play_url,
                    telegram_status=SocialQueuePost.STATUS_PENDING,
                )
        except IntegrityError:
            post = SocialQueuePost.objects.filter(
                source=SocialQueuePost.SOURCE_CENSORLY,
                ladder_date=censorly.censorly_date,
            ).first()
    if post is None:
        return None
    if post.telegram_ok and not force:
        _queue_other_networks(
            post, post.telegram_scheduled_for or publish_at_for_date(censorly.censorly_date),
        )
        return post
    claim_token = claim_telegram_post(post.pk, force=force)
    if claim_token is None:
        post.refresh_from_db()
        if post.telegram_ok and not force:
            _queue_other_networks(
                post, post.telegram_scheduled_for or publish_at_for_date(censorly.censorly_date),
            )
        return post

    try:
        image_png = render_censorly_teaser_png(
            number=censorly.number, url=censorly.play_url,
        )
        caption = build_caption(censorly)
    except Exception as exc:
        logger.exception('Censorly channel render failed for №%s', censorly.number)
        error = describe_render_failure(
            'censorly №{}'.format(censorly.number), censorly.play_url, exc,
        )
        complete_telegram_publish(
            post.pk, claim_token, status=SocialQueuePost.STATUS_FAILED,
            error=error, count_attempt=True,
        )
        if telegram_admin_configured():
            try:
                send_message(admin_chat_id(), admin_render_failure_message(error))
            except Exception:
                logger.exception('Failed to notify admin about Censorly render failure')
        post.refresh_from_db()
        return post

    post.ladder_number = censorly.number
    post.play_url = censorly.play_url
    post.caption = caption
    try:
        post.set_image_bytes(image_png, filename='censorly-{}.png'.format(censorly.number))
        if not update_claimed_telegram_post(
            post.pk, claim_token, ladder_number=censorly.number,
            play_url=censorly.play_url, caption=caption, image=post.image.name,
        ):
            post.refresh_from_db()
            return post
        post.refresh_from_db()
        schedule_at = None if immediate else publish_at_for_date(censorly.censorly_date)
        if schedule_at is not None and schedule_at <= moscow_now(now) + timedelta(seconds=10):
            error = (
                '18:30 MSK already passed for {}; refusing immediate send. '
                'Use --now only for an intentional immediate post.'.format(censorly.censorly_date)
            )
            complete_telegram_publish(
                post.pk, claim_token, status=SocialQueuePost.STATUS_FAILED,
                error=error, scheduled_for=schedule_at,
            )
            if telegram_admin_configured():
                try:
                    send_message(admin_chat_id(), admin_post_failure_message(error))
                except Exception:
                    logger.exception('Failed to notify admin about late Censorly post')
            post.refresh_from_db()
            return post
        publish_telegram(
            post, immediate=immediate, schedule_at=schedule_at,
            force=force, claim_token=claim_token,
        )
    except Exception as exc:
        logger.exception('Censorly channel post preparation failed for №%s', censorly.number)
        return _mark_failed(post, claim_token, stage='prepare_or_publish', exc=exc)

    completed_by_caller = bool(getattr(post, '_telegram_completion_applied', False))
    post.refresh_from_db()
    if post.telegram_status == SocialQueuePost.STATUS_FAILED:
        error = post.telegram_error or 'Telegram publish failed without details'
        if telegram_admin_configured():
            try:
                send_message(admin_chat_id(), admin_post_failure_message(error))
            except Exception:
                logger.exception('Failed to notify admin about Censorly Telegram failure')
        return post
    if completed_by_caller and notify_admin and telegram_admin_configured() and post.telegram_ok:
        when = (
            'сразу' if post.telegram_status == SocialQueuePost.STATUS_SENT
            else 'в отложенные на 18:30 МСК'
        )
        caption_preview = 'Канал @interoves: Цензурка №{} — {}\n\n{}'.format(
            post.ladder_number, when, caption,
        )
        try:
            send_photo(
                admin_chat_id(), post.image_bytes(), caption=caption_preview,
                filename='censorly-{}.png'.format(post.ladder_number),
            )
        except Exception:
            logger.exception('Admin preview for Censorly channel post failed')
    if post.telegram_ok:
        if immediate:
            from games.social.publish import publish_instagram, publish_threads, publish_twitter
            publish_twitter(post, force=force)
            publish_instagram(post, force=force)
            publish_threads(post, force=force)
        else:
            _queue_other_networks(post, schedule_at or publish_at_for_date(censorly.censorly_date), force=force)
        post.refresh_from_db()
    return post


def publish_censorly_channel_post(*, force: bool = False, **kwargs):
    """Intentional immediate send, without Telegram's scheduled delivery."""
    return schedule_censorly_channel_post(force=force, immediate=True, **kwargs)


def _in_window(msk: datetime) -> bool:
    start = msk.replace(hour=PREPARE_HOUR, minute=PREPARE_MINUTE, second=0, microsecond=0)
    return start <= msk < start + timedelta(minutes=WINDOW_MINUTES)


def process_censorly_channel_tick(now: datetime | None = None) -> dict[str, int]:
    """Minute tick: prepare at 00:15 for delivery two hours after the ladder teaser."""
    msk = moscow_now(now)
    stats = {'scheduled': 0, 'skipped': 1}
    if not _in_window(msk):
        return stats
    before = SocialQueuePost.objects.filter(
        source=SocialQueuePost.SOURCE_CENSORLY,
        ladder_date=msk.date(),
        telegram_status__in=(SocialQueuePost.STATUS_SCHEDULED, SocialQueuePost.STATUS_SENT),
    ).exists()
    post = schedule_censorly_channel_post(now=now, notify_admin=True)
    if post and post.telegram_ok and not before:
        stats.update(scheduled=1, skipped=0)
    return stats
