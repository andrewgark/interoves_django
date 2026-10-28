"""Daily salad post for the public Telegram channel via SocialQueuePost."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from games.daily.catalog.word_salad import (
    current_word_salad_number,
    is_word_salad_number_published,
)
from games.daily.page.section import WORD_SALAD_GAME_ID
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
from games.ops.telegram.images.salad import render_word_salad_teaser_png, word_salad_last_screenshot_url
from games.ops.telegram.links import admin_url
from games.ops.telegram.transport.api import send_message, send_photo
from games.puzzles.salad.rules import theme_from_text
from games.sections.paths import section_play_path

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
    from games.ops.telegram.channels.daily_post import moscow_now as _moscow_now

    return _moscow_now(now)


def publish_at_for_date(salad_date: date) -> datetime:
    """14:30 MSK on the salad's calendar day (two hours before the ladder teaser)."""
    return publish_clock(salad_date, PUBLISH_HOUR, PUBLISH_MINUTE)


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
    play_url = admin_url(section_play_path(WORD_SALAD_GAME_ID, number))
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
    theme = theme_from_text(salad.task.text)
    author = str((salad.task.tags or {}).get('author') or '').strip()
    lines = [
        '🥗 <b>Салатик №{}</b>'.format(_escape(salad.number)),
        '',
    ]
    if theme:
        lines.append('Тема: {}'.format(_escape(theme)))
    if author:
        lines.append('Автор: {}'.format(_escape(author)))
    if theme or author:
        lines.append('')
    lines.append(salad.play_url)
    return '\n'.join(lines)


def salad_channel_ready() -> bool:
    return channel_ready()


def _render(edition: TodaySalad) -> bytes:
    return render_word_salad_teaser_png(
        edition.task,
        salad_number=edition.number,
        url=edition.play_url,
        fallback_to_pillow=False,
    )


def _send_photo(*args, **kwargs):
    return send_photo(*args, **kwargs)


def _send_message(*args, **kwargs):
    return send_message(*args, **kwargs)


SALAD_CHANNEL_POST = ChannelPostSpec(
    key='salad',
    source=SocialQueuePost.SOURCE_WORD_SALAD,
    stats_key='salad_scheduled',
    prepare_hour=PREPARE_HOUR,
    prepare_minute=PREPARE_MINUTE,
    window_minutes=WINDOW_MINUTES,
    sync_task=True,
    warn_when_late=True,
    resolve_today=resolve_today_salad,
    edition_date=lambda edition: edition.salad_date,
    build_caption=build_caption,
    render=_render,
    render_failure_url=lambda _edition: word_salad_last_screenshot_url(),
    publish_at=publish_at_for_date,
    send_photo=_send_photo,
    send_message=_send_message,
    skip_unconfigured_message=(
        'Salad channel schedule skipped: need TELEGRAM_CHANNEL_CHAT_ID + user MTProto session'
    ),
    skip_unpublished_message='Salad channel schedule skipped: no published salad for today',
    skip_unpublished_level='warning',
    failure_label='Салатик №{}',
    render_failure_kind='salad №{}',
    late_error=(
        '14:30 MSK already passed for {}; refusing to post immediately. '
        'Use --now only if you really want to post now.'
    ),
    scheduled_when='в отложенные на 14:30 МСК',
    admin_channel_preview='Канал @interoves: салатик №{} — {}\n\n{}',
    image_filename='salad-{}.png',
    render_failed_log='Salad channel render failed for №%s',
    prepare_failed_log='Salad channel post preparation failed for №%s',
    notify_render_failure_log='Failed to notify admin about salad render failure',
    notify_late_log='Failed to notify admin about late salad post',
    notify_post_failure_log='Failed to notify admin about salad post failure',
    notify_telegram_failure_log='Failed to notify admin about salad Telegram failure',
    admin_preview_failed_log='Admin preview for salad channel post failed',
    tick_failure_message='Salad channel tick failed',
    missing_today_message='Нет опубликованного салатика на сегодня',
    preview_success='Салатик №{number} → admin chat (message_id={message_id})',
    preview_render_failed_log='Salad admin preview render failed',
)
register_channel_post(SALAD_CHANNEL_POST)


def preview_salad_to_admin(*, now: datetime | None = None) -> tuple[bool, str]:
    """Render today's salad teaser and send it to the admin bot chat only."""
    return preview_channel_post(SALAD_CHANNEL_POST, now=now)


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
    return schedule_channel_post(
        SALAD_CHANNEL_POST,
        now=now,
        force=force,
        immediate=immediate,
        notify_admin=notify_admin,
    )


def prepare_salad_channel_post(**kwargs):
    return schedule_salad_channel_post(**kwargs)


def publish_salad_channel_post(*, force: bool = False, **kwargs):
    """Immediate publish (no schedule) — for --now / manual catch-up."""
    return schedule_salad_channel_post(force=force, immediate=True, **kwargs)


def process_salad_channel_tick(now: datetime | None = None) -> dict[str, Any]:
    """
    Minute cron: at 00:15 MSK schedule today's salad for 14:30 MSK via MTProto.
    Telegram itself publishes at schedule_date — no 14:30 job needed for TG.
    """
    return SALAD_CHANNEL_POST.process_tick(now=now)
