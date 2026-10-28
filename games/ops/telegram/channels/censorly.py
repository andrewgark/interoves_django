"""Daily Censorly social post through SocialQueuePost."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from games.daily.catalog.censorly import (
    CENSORLY_GAME_ID,
    current_censorly_number,
    is_censorly_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.ops.social.models import SocialQueuePost
from games.ops.telegram.channels.daily_post import (
    ChannelPostSpec,
    channel_ready,
    deliver_admin_photo,
    publish_clock,
    register_channel_post,
    schedule_channel_post,
)
from games.ops.telegram.images.censorly import render_censorly_teaser_png
from games.ops.telegram.links import admin_url
from games.ops.telegram.transport.api import send_message, send_photo
from games.ops.telegram.transport.config import telegram_admin_configured
from games.sections.paths import section_play_path

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
    from games.ops.telegram.channels.daily_post import moscow_now as _moscow_now

    return _moscow_now(now)


def publish_at_for_date(censorly_date: date) -> datetime:
    """18:30 MSK, two hours after the ladder's 16:30 MSK social post."""
    return publish_clock(censorly_date, PUBLISH_HOUR, PUBLISH_MINUTE)


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
    from games.puzzles.censorly.play import puzzle_from_task

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
    return channel_ready()


def _render(edition: TodayCensorly) -> bytes:
    return render_censorly_teaser_png(number=edition.number, url=edition.play_url)


def _send_photo(*args, **kwargs):
    return send_photo(*args, **kwargs)


def _send_message(*args, **kwargs):
    return send_message(*args, **kwargs)


CENSORLY_CHANNEL_POST = ChannelPostSpec(
    key='censorly',
    source=SocialQueuePost.SOURCE_CENSORLY,
    stats_key='censorly_scheduled',
    prepare_hour=PREPARE_HOUR,
    prepare_minute=PREPARE_MINUTE,
    window_minutes=WINDOW_MINUTES,
    sync_task=False,
    warn_when_late=False,
    resolve_today=resolve_today_censorly,
    edition_date=lambda edition: edition.censorly_date,
    build_caption=build_caption,
    render=_render,
    render_failure_url=lambda edition: edition.play_url,
    publish_at=publish_at_for_date,
    send_photo=_send_photo,
    send_message=_send_message,
    skip_unconfigured_message='Censorly channel schedule skipped: Telegram channel is not configured',
    skip_unpublished_message='Censorly channel schedule skipped: no published daily task',
    skip_unpublished_level='info',
    failure_label='Цензурка №{}',
    render_failure_kind='censorly №{}',
    late_error=(
        '18:30 MSK already passed for {}; refusing immediate send. '
        'Use --now only for an intentional immediate post.'
    ),
    scheduled_when='в отложенные на 18:30 МСК',
    admin_channel_preview='Канал @interoves: Цензурка №{} — {}\n\n{}',
    image_filename='censorly-{}.png',
    render_failed_log='Censorly channel render failed for №%s',
    prepare_failed_log='Censorly channel post preparation failed for №%s',
    notify_render_failure_log='Failed to notify admin about Censorly render failure',
    notify_late_log='Failed to notify admin about late Censorly post',
    notify_post_failure_log='Failed to notify admin about Censorly post failure',
    notify_telegram_failure_log='Failed to notify admin about Censorly Telegram failure',
    admin_preview_failed_log='Admin preview for Censorly channel post failed',
    tick_failure_message='Censorly channel tick failed',
    missing_today_message='Нет опубликованной Цензурки на сегодня',
    preview_success='Цензурка №{number} → admin chat (message_id={message_id})',
    preview_render_failed_log='Censorly admin preview render failed',
)
register_channel_post(CENSORLY_CHANNEL_POST)


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
    return deliver_admin_photo(
        _send_photo,
        image_png=image_png,
        caption=caption,
        filename='censorly-{}.png'.format(target_number),
        success_message='Цензурка №{} → admin chat (message_id={{message_id}})'.format(target_number),
    )


def schedule_censorly_channel_post(
    *, now: datetime | None = None, force: bool = False,
    immediate: bool = False, notify_admin: bool = True,
) -> SocialQueuePost | None:
    """Render today's opening article, schedule Telegram for 18:30 MSK."""
    return schedule_channel_post(
        CENSORLY_CHANNEL_POST,
        now=now,
        force=force,
        immediate=immediate,
        notify_admin=notify_admin,
    )


def publish_censorly_channel_post(*, force: bool = False, **kwargs):
    """Intentional immediate send, without Telegram's scheduled delivery."""
    return schedule_censorly_channel_post(force=force, immediate=True, **kwargs)


def process_censorly_channel_tick(now: datetime | None = None) -> dict[str, int]:
    """Minute tick: prepare at 00:15 for delivery two hours after the ladder teaser."""
    return CENSORLY_CHANNEL_POST.process_tick(now=now)
