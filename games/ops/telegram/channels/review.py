"""22:00 MSK admin review of the next day's recurring editions."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import IntegrityError
from django.utils import timezone

from games.daily.page.section import (
    ALPHABETTY_SCHEDULE,
    CENSORLY_SCHEDULE,
    LADDER_SCHEDULE,
    MOSCOW,
    WORD_SALAD_SCHEDULE,
)
from games.models import Game, GameTaskGroup
from games.ops.telegram.bot.notify import send_admin_message
from games.ops.telegram.channels.puzzle_text import (
    PUZZLE_LINES,
    author_html,
    escape,
    first_task,
)
from games.ops.telegram.links import SECTION_SUPPORT_PATHS, support_url
from games.ops.telegram.models import TelegramDailyReview

REVIEW_HOUR = 22
DAILY_SCHEDULES = (
    LADDER_SCHEDULE,
    ALPHABETTY_SCHEDULE,
    WORD_SALAD_SCHEDULE,
    CENSORLY_SCHEDULE,
)

MONTHS_RU = (
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
WEEKDAYS_RU = (
    'понедельник', 'вторник', 'среда', 'четверг',
    'пятница', 'суббота', 'воскресенье',
)

# game id -> (emoji, title, task type)
SECTION_META = {
    LADDER_SCHEDULE.game_id: ('🪜', 'Лесенка', 'raddle'),
    ALPHABETTY_SCHEDULE.game_id: ('🔤', 'Алфавитка', 'alphabetty'),
    WORD_SALAD_SCHEDULE.game_id: ('🥗', 'Салатик', 'word_salad'),
    CENSORLY_SCHEDULE.game_id: ('📰', 'Цензурка', 'censorly'),
}


def _date_label(target_date: date) -> str:
    return '{}, {} {} {}'.format(
        WEEKDAYS_RU[target_date.weekday()],
        target_date.day,
        MONTHS_RU[target_date.month],
        target_date.year,
    )


def _resolve_edition(schedule, target_date: date):
    game = Game.objects.filter(pk=schedule.game_id).first()
    if game is None:
        return None, None, None, 'игра не найдена'
    number = schedule.number_for_date(game, target_date)
    if number is None:
        return game, None, None, 'не настроена дата начала публикаций'
    link = (
        GameTaskGroup.objects
        .filter(game=game, number=str(number))
        .select_related('task_group')
        .first()
    )
    if link is None:
        return game, number, None, 'нет выпуска №{}'.format(number)
    return game, number, link, ''


def edition_section(schedule, number: int, link: GameTaskGroup) -> tuple[list[str], bool]:
    """One edition: title, author, the puzzle with spoilers, a link to its /support page."""
    emoji, title, task_type = SECTION_META[schedule.game_id]
    header = '{} <b>{} №{}</b>'.format(emoji, title, number)
    task = first_task(link.task_group_id, task_type)
    if task is None:
        return [header, '⚠️ Нет задания в выпуске.'], False
    body, ready = PUZZLE_LINES[task_type](task)
    lines = [
        header,
        'Автор: {}'.format(author_html(task_group_id=link.task_group_id, link_id=link.pk, task=task)),
        *body,
        '',
        '<a href="{}">Открыть в /support</a>'.format(escape(support_url(SECTION_SUPPORT_PATHS[schedule.game_id]))),
    ]
    return lines, ready


def build_daily_review(target_date: date) -> tuple[str, dict]:
    """Return an HTML review and editor keyboard for one Moscow calendar date."""
    sections: list[list[str]] = []
    ready_count = 0
    schedules = list(DAILY_SCHEDULES)

    for schedule in schedules:
        emoji, title, _task_type = SECTION_META[schedule.game_id]
        _game, number, link, error = _resolve_edition(schedule, target_date)
        if error:
            sections.append([
                '{} <b>{}</b>'.format(emoji, title),
                '⚠️ {}'.format(escape(error)),
            ])
            continue
        lines, ready = edition_section(schedule, number, link)
        sections.append(lines)
        ready_count += int(ready)

    lines = [
        '🗓 <b>Задания на завтра</b>',
        escape(_date_label(target_date)),
        '',
        'Публикация в 00:00 МСК. Ответы скрыты — нажмите, чтобы раскрыть.',
    ]
    for section in sections:
        lines.extend(['', '──────────', ''])
        lines.extend(section)
    lines.extend(['', '──────────', ''])
    if ready_count == len(schedules):
        lines.append('✅ Все {} выпуска на месте.'.format(len(schedules)))
    else:
        lines.append('⚠️ Готово выпусков: {} из {}. Проверьте предупреждения выше.'.format(
            ready_count, len(schedules),
        ))

    keyboard = {'inline_keyboard': [[
        {
            'text': '{} {}'.format(SECTION_META[s.game_id][0], SECTION_META[s.game_id][1]),
            'url': support_url(SECTION_SUPPORT_PATHS[s.game_id]),
        }
        for s in schedules
    ]]}
    return '\n'.join(lines), keyboard


def _in_review_window(msk_now: datetime) -> bool:
    # Cron runs every minute, but can be delayed by deploys or instance load.
    # Keep retrying through the rest of the Moscow day so a missed first few
    # ticks do not lose this date's review.
    return msk_now.hour >= REVIEW_HOUR


def process_daily_review_tick(now: datetime | None = None) -> dict[str, int]:
    """Send tomorrow's review once from 22:00 until midnight MSK."""
    now = now or timezone.now()
    msk_now = now.astimezone(MOSCOW)
    stats = {'sent': 0, 'skipped': 1}
    if not _in_review_window(msk_now):
        return stats

    target_date = msk_now.date() + timedelta(days=1)
    try:
        marker, created = TelegramDailyReview.objects.get_or_create(review_date=target_date)
    except IntegrityError:
        return stats
    if not created:
        return stats

    try:
        text, keyboard = build_daily_review(target_date)
        if send_admin_message(text, reply_markup=keyboard):
            stats['sent'] = 1
            stats['skipped'] = 0
            return stats
    except Exception:
        # The outer scheduler logs the traceback. Removing the claim lets the next
        # minute (or another EB host) retry safely.
        marker.delete()
        raise

    marker.delete()
    return stats
