"""Reusable date/month model for archives of scheduled games.

Game-specific views only adapt their records to ``items``.  This module knows
nothing about Game, Task, attempts, or any particular daily game.
"""

from __future__ import annotations

import calendar
from datetime import date


MONTH_NAMES = (
    '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)


def month_key(value: date) -> str:
    return value.strftime('%Y-%m')


def parse_month(value) -> tuple[int, int] | None:
    try:
        year, month = (int(part) for part in str(value).split('-', 1))
        if 1 <= month <= 12 and 1 <= year <= 9999:
            return year, month
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def build_daily_archive_context(*, items, requested_month=None, today=None,
                                archive_url='', game_label='Задание',
                                completed_keys=(), archive_query='',
                                calendar_id='daily-archive'):
    """Build calendar/navigation context from neutral dated archive items.

    ``items`` contains ``date``, ``key``, ``anchor`` and ``href``; arbitrary
    game-specific values may be carried alongside them by the view.
    """
    today = today or date.today()
    completed_keys = {str(key) for key in completed_keys}
    items = [item for item in items if isinstance(item.get('date'), date)]
    items_by_date = {item['date']: item for item in items}
    months = sorted({(item['date'].year, item['date'].month) for item in items})
    if not months:
        return {'daily_archive': False}

    requested = parse_month(requested_month)
    selected = requested if requested in months else (
        (today.year, today.month) if (today.year, today.month) in months else months[-1]
    )
    year, month = selected
    month_date = date(year, month, 1)

    def url_for(month_value):
        suffix = '&{}'.format(archive_query) if archive_query else ''
        return '{}?month={}{}'.format(archive_url, month_key(month_value), suffix)

    month_index = months.index(selected)
    previous = date(*months[month_index - 1], 1) if month_index else None
    following = date(*months[month_index + 1], 1) if month_index + 1 < len(months) else None

    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for day in week:
            item = items_by_date.get(day)
            available = item is not None and day.month == month
            completed = available and str(item.get('key')) in completed_keys
            label = '{} {} {}, {}'.format(day.day, MONTH_NAMES[day.month].lower(), day.year, game_label)
            if item and item.get('number'):
                label += ' №{}'.format(item['number'])
            if completed:
                label += ', выполнено'
            elif available:
                label += ', не выполнено'
            else:
                label += ', задания нет'
            cells.append({
                'date': day,
                'number': day.day,
                'is_current_month': day.month == month,
                'is_available': available,
                'is_completed': completed,
                'is_today': day == today,
                # A calendar day navigates within the archive.  The item's
                # play URL remains available to the game-specific card.
                'href': url_for(month_date) if available else '',
                'anchor': item.get('anchor') if available else '',
                'aria_label': label,
            })
        weeks.append(cells)

    return {
        'daily_archive': True,
        'daily_archive_month': month_key(month_date),
        'daily_archive_month_label': '{} {}'.format(MONTH_NAMES[month], year),
        'daily_archive_months': [
            {'key': '{}-{:02d}'.format(y, m), 'label': '{} {}'.format(MONTH_NAMES[m], y),
             'href': url_for(date(y, m, 1)), 'is_selected': (y, m) == selected}
            for y, m in months
        ],
        'daily_archive_previous': {
            'label': 'Предыдущий месяц', 'href': url_for(previous)
        } if previous else None,
        'daily_archive_next': {
            'label': 'Следующий месяц', 'href': url_for(following)
        } if following else None,
        'daily_archive_weeks': weeks,
        'daily_archive_url': archive_url,
        'daily_archive_calendar_id': calendar_id,
        'daily_archive_items': [item for item in items if item['date'].year == year and item['date'].month == month],
        'daily_archive_selected': selected,
    }
