"""Daily ladder post for the public Telegram channel via SocialQueuePost."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from games.daily.catalog.ladder import (
    LADDER_GAME_ID,
    current_ladder_number,
    is_ladder_number_published,
)
from games.models import Game, GameTaskGroup, Task
from games.ops.social.models import SocialQueuePost
from games.ops.telegram.channels.daily_post import (
    ChannelPostSpec,
    channel_ready,
    preview_channel_post,
    publish_clock,
    register_channel_post,
    schedule_channel_post,
)
from games.ops.telegram.images.ladder import ladder_last_screenshot_url, render_ladder_teaser_png
from games.ops.telegram.links import admin_url
from games.ops.telegram.transport.api import send_message, send_photo

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
    from games.ops.telegram.channels.daily_post import moscow_now as _moscow_now

    return _moscow_now(now)


def publish_at_for_date(ladder_date: date) -> datetime:
    """16:30 MSK on the ladder's calendar day."""
    return publish_clock(ladder_date, PUBLISH_HOUR, PUBLISH_MINUTE)


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
    play_url = admin_url('/ladder/{}/'.format(number))
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
    play_url = admin_url('/ladder/{}/'.format(number))
    return TodayLadder(
        game=game,
        number=number,
        link=link,
        task=task,
        play_url=play_url,
        ladder_date=msk.date(),
    )


def build_caption(ladder: TodayLadder, *, parsed=None) -> str:
    from games.puzzles.raddle.engine import parse_raddle_data

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
    return channel_ready()


def _render(edition: TodayLadder) -> bytes:
    return render_ladder_teaser_png(
        edition.task,
        ladder_number=edition.number,
        url=edition.play_url,
        fallback_to_pillow=False,
    )


def _send_photo(*args, **kwargs):
    return send_photo(*args, **kwargs)


def _send_message(*args, **kwargs):
    return send_message(*args, **kwargs)


LADDER_CHANNEL_POST = ChannelPostSpec(
    key='ladder',
    source=SocialQueuePost.SOURCE_LADDER,
    stats_key='ladder_scheduled',
    prepare_hour=PREPARE_HOUR,
    prepare_minute=PREPARE_MINUTE,
    window_minutes=WINDOW_MINUTES,
    sync_task=True,
    warn_when_late=True,
    resolve_today=resolve_today_ladder,
    edition_date=lambda edition: edition.ladder_date,
    build_caption=build_caption,
    render=_render,
    render_failure_url=lambda _edition: ladder_last_screenshot_url(),
    publish_at=publish_at_for_date,
    send_photo=_send_photo,
    send_message=_send_message,
    skip_unconfigured_message=(
        'Ladder channel schedule skipped: need TELEGRAM_CHANNEL_CHAT_ID + user MTProto session'
    ),
    skip_unpublished_message='Ladder channel schedule skipped: no published ladder for today',
    skip_unpublished_level='warning',
    failure_label='Лесенка №{}',
    render_failure_kind='ladder №{}',
    late_error=(
        '16:30 MSK already passed for {}; refusing to post immediately. '
        'Use --now only if you really want to publish now.'
    ),
    scheduled_when='в отложенные на 16:30 МСК',
    admin_channel_preview='Канал @interoves: лесенка №{} — {}\n\n{}',
    image_filename='ladder-{}.png',
    render_failed_log='Ladder channel render failed for №%s',
    prepare_failed_log='Ladder channel post preparation failed for №%s',
    notify_render_failure_log='Failed to notify admin about ladder render failure',
    notify_late_log='Failed to notify admin about late ladder post',
    notify_post_failure_log='Failed to notify admin about ladder post failure',
    notify_telegram_failure_log='Failed to notify admin about ladder Telegram failure',
    admin_preview_failed_log='Admin preview for ladder channel post failed',
    tick_failure_message='Ladder channel tick failed',
    missing_today_message='Нет опубликованной лесенки на сегодня',
    preview_success='Лесенка №{number} → admin chat (message_id={message_id})',
    preview_render_failed_log='Ladder admin preview render failed',
)
register_channel_post(LADDER_CHANNEL_POST)


def preview_ladder_to_admin(*, now: datetime | None = None) -> tuple[bool, str]:
    """Render today's ladder teaser and send it to the admin bot chat only."""
    return preview_channel_post(LADDER_CHANNEL_POST, now=now)


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
    return schedule_channel_post(
        LADDER_CHANNEL_POST,
        now=now,
        force=force,
        immediate=immediate,
        notify_admin=notify_admin,
    )


def prepare_ladder_channel_post(**kwargs):
    return schedule_ladder_channel_post(**kwargs)


def publish_ladder_channel_post(*, force: bool = False, **kwargs):
    """Immediate publish (no schedule) — for --now / manual catch-up."""
    return schedule_ladder_channel_post(force=force, immediate=True, **kwargs)


def process_ladder_channel_tick(now: datetime | None = None) -> dict[str, Any]:
    """
    Minute cron: at 00:15 MSK schedule today's ladder for 16:30 MSK via MTProto.
    Telegram itself publishes at schedule_date — no 16:30 job needed.
    """
    return LADDER_CHANNEL_POST.process_tick(now=now)
