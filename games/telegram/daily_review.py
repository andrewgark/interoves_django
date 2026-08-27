"""22:00 MSK admin review of the next day's recurring editions."""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta

from django.db import IntegrityError
from django.utils import timezone
from django.utils.html import strip_tags

from games.daily_section import (
    ALPHABETTY_SCHEDULE,
    LADDER_SCHEDULE,
    MOSCOW,
    WEEK_TASK_SCHEDULE,
    WORD_SALAD_SCHEDULE,
)
from games.models import Game, GameTaskGroup, Task
from games.telegram.game_urls import admin_url, task_admin_url
from games.telegram.models import TelegramDailyReview
from games.telegram.notify import send_admin_message

REVIEW_HOUR = 22
REVIEW_WINDOW_MINUTES = 5
DAILY_SCHEDULES = (
    LADDER_SCHEDULE,
    ALPHABETTY_SCHEDULE,
    WORD_SALAD_SCHEDULE,
)

MONTHS_RU = (
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
WEEKDAYS_RU = (
    'понедельник', 'вторник', 'среда', 'четверг',
    'пятница', 'суббота', 'воскресенье',
)

SECTION_META = {
    LADDER_SCHEDULE.game_id: ('🪜', 'Лесенка', '/support/ladders/'),
    ALPHABETTY_SCHEDULE.game_id: ('🔤', 'Алфавитка', '/support/alphabetty/'),
    WORD_SALAD_SCHEDULE.game_id: ('🥗', 'Салатик', '/support/salad/'),
    WEEK_TASK_SCHEDULE.game_id: ('📅', 'Задание недели', '/support/week-tasks/'),
}


def _escape(value) -> str:
    return html.escape(str(value or ''), quote=False)


def _plain_text(value) -> str:
    return ' '.join(html.unescape(strip_tags(str(value or ''))).split())


def _spoiler(value) -> str:
    return '<tg-spoiler>{}</tg-spoiler>'.format(_escape(value))


def _date_label(target_date: date) -> str:
    return '{}, {} {} {}'.format(
        WEEKDAYS_RU[target_date.weekday()],
        target_date.day,
        MONTHS_RU[target_date.month],
        target_date.year,
    )


def _task_for_link(link: GameTaskGroup, preferred_type: str) -> Task | None:
    tasks = Task.objects.visible().filter(task_group_id=link.task_group_id)
    return tasks.filter(task_type=preferred_type).order_by('id').first() or tasks.order_by('id').first()


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


def _format_ladder(number: int, link: GameTaskGroup) -> tuple[list[str], bool]:
    from games.raddle import parse_raddle_data

    task = _task_for_link(link, 'raddle')
    if task is None:
        return ['⚠️ Нет задания в выпуске.'], False
    parsed = parse_raddle_data(task)
    if not parsed:
        return ['⚠️ Не удалось разобрать данные лесенки.'], False

    words = parsed.get('words') or []
    hints = parsed.get('hints') or []
    lines = ['🪜 <b>Лесенка №{}</b>'.format(number)]
    intro = _plain_text(task.text)
    if intro:
        lines.append('Вводная: {}'.format(_escape(intro)))
    author = (task.tags or {}).get('author')
    if author:
        lines.append('Автор: {}'.format(_escape(author)))
    lines.extend(['', '<b>Цепочка:</b>'])
    if words:
        lines.append('Старт: {}'.format(_spoiler(words[0])))
    for index, hint in enumerate(hints, start=1):
        answer = words[index] if index < len(words) else '⚠️ нет ответа'
        lines.append('{}. {} → {}'.format(index, _escape(hint), _spoiler(answer)))
    lines.extend(['', '<a href="{}">Редактировать выпуск</a>'.format(task_admin_url(task))])
    return lines, bool(words) and len(words) == len(hints) + 1


def _format_alphabetty(number: int, link: GameTaskGroup) -> tuple[list[str], bool]:
    task = _task_for_link(link, 'alphabetty')
    if task is None:
        return ['⚠️ Нет задания в выпуске.'], False
    answer = (task.answer or task.checker_data or '').strip().splitlines()
    answer = answer[0].strip() if answer else ''
    lines = [
        '🔤 <b>Алфавитка №{}</b>'.format(number),
        'Задание: найти загаданное слово бинарным поиском.',
        'Ответ: {}'.format(_spoiler(answer or '⚠️ не заполнен')),
        '',
        '<a href="{}">Редактировать выпуск</a>'.format(task_admin_url(task)),
    ]
    return lines, bool(answer)


def _format_salad(number: int, link: GameTaskGroup) -> tuple[list[str], bool]:
    from games.word_salad import format_grid_text, parse_task_data, theme_from_text

    task = _task_for_link(link, 'word_salad')
    if task is None:
        return ['⚠️ Нет задания в выпуске.'], False
    try:
        grid, words = parse_task_data(task.checker_data, task.answer or '')
    except ValueError:
        return ['⚠️ Не удалось разобрать сетку и ответы салатика.'], False

    lines = ['🥗 <b>Салатик №{}</b>'.format(number)]
    theme = theme_from_text(task.text)
    if theme:
        lines.append('Тема: {}'.format(_escape(theme)))
    lines.extend([
        '<pre>{}</pre>'.format(_escape(format_grid_text(grid))),
        'Ответы ({}): {}'.format(len(words), _spoiler(' · '.join(words) or '⚠️ не заполнены')),
        '',
        '<a href="{}">Редактировать выпуск</a>'.format(task_admin_url(task)),
    ])
    return lines, len(grid) == 16 and bool(words)


def _format_week_task(number: int, link: GameTaskGroup) -> tuple[list[str], bool]:
    tasks = list(
        Task.objects.visible()
        .filter(task_group_id=link.task_group_id)
        .order_by('number', 'id')
    )
    if not tasks:
        return ['⚠️ Нет заданий в выпуске.'], False

    lines = ['📅 <b>Задание недели №{}</b>'.format(number)]
    ready = True
    for task in tasks:
        condition = _plain_text(task.text) or '⚠️ условие не заполнено'
        answer = (task.answer or '').strip()
        ready = ready and bool(_plain_text(task.text)) and bool(answer)
        lines.extend([
            '',
            '<b>{}.</b> {}'.format(_escape(task.number), _escape(condition)),
            'Ответ: {}'.format(_spoiler(answer or '⚠️ не заполнен')),
        ])
    lines.extend(['', '<a href="{}">Редактировать выпуск</a>'.format(
        admin_url('/support/week-tasks/{}/'.format(link.pk)),
    )])
    return lines, ready


FORMATTERS = {
    LADDER_SCHEDULE.game_id: _format_ladder,
    ALPHABETTY_SCHEDULE.game_id: _format_alphabetty,
    WORD_SALAD_SCHEDULE.game_id: _format_salad,
    WEEK_TASK_SCHEDULE.game_id: _format_week_task,
}


def _schedules_for_date(target_date: date):
    schedules = list(DAILY_SCHEDULES)
    game = Game.objects.filter(pk=WEEK_TASK_SCHEDULE.game_id).first()
    if game is None:
        return schedules
    number = WEEK_TASK_SCHEDULE.number_for_date(game, target_date)
    publish_at = WEEK_TASK_SCHEDULE.publish_at(game, number) if number is not None else None
    if publish_at is not None and publish_at.date() == target_date:
        schedules.append(WEEK_TASK_SCHEDULE)
    return schedules


def build_daily_review(target_date: date) -> tuple[str, dict]:
    """Return an HTML review and editor keyboard for one Moscow calendar date."""
    sections: list[list[str]] = []
    ready_count = 0
    schedules = _schedules_for_date(target_date)

    for schedule in schedules:
        emoji, title, _dashboard_path = SECTION_META[schedule.game_id]
        _game, number, link, error = _resolve_edition(schedule, target_date)
        if error:
            sections.append([
                '{} <b>{}</b>'.format(emoji, title),
                '⚠️ {}'.format(_escape(error)),
            ])
            continue
        lines, ready = FORMATTERS[schedule.game_id](number, link)
        sections.append(lines)
        ready_count += int(ready)

    lines = [
        '🗓 <b>Задания на завтра</b>',
        _escape(_date_label(target_date)),
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

    button_rows = [[
        {
            'text': '{} {}'.format(SECTION_META[s.game_id][0], SECTION_META[s.game_id][1]),
            'url': admin_url(SECTION_META[s.game_id][2]),
        }
        for s in schedules if s.game_id != WEEK_TASK_SCHEDULE.game_id
    ]]
    if WEEK_TASK_SCHEDULE in schedules:
        button_rows.append([{
            'text': '{} {}'.format(
                SECTION_META[WEEK_TASK_SCHEDULE.game_id][0],
                SECTION_META[WEEK_TASK_SCHEDULE.game_id][1],
            ),
            'url': admin_url(SECTION_META[WEEK_TASK_SCHEDULE.game_id][2]),
        }])
    keyboard = {'inline_keyboard': button_rows}
    return '\n'.join(lines), keyboard


def _in_review_window(msk_now: datetime) -> bool:
    return msk_now.hour == REVIEW_HOUR and msk_now.minute < REVIEW_WINDOW_MINUTES


def process_daily_review_tick(now: datetime | None = None) -> dict[str, int]:
    """Send tomorrow's review once during the 22:00–22:04 MSK cron window."""
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
