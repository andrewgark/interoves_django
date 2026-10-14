"""Reusable date/month model for archives of scheduled games."""

from __future__ import annotations

import calendar
from datetime import date
from urllib.parse import urlencode

from django.utils import timezone


MONTH_NAMES = (
    '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)
MONTH_NAMES_GENITIVE = (
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def archive_query_string(query_params, *, excluded=('month',)) -> str:
    """Encode request query parameters while omitting archive-owned keys."""
    values = []
    for key in query_params:
        if key in excluded:
            continue
        values.extend((key, value) for value in query_params.getlist(key))
    return urlencode(values)


def build_daily_archive_items(links, *, game_id, publish_at_for_number,
                              href_for_number, timezone_name,
                              number_for_link=None):
    """Build neutral calendar items from game-specific published links."""
    items = []
    for link in links:
        number = number_for_link(link) if number_for_link else link.number
        published_at = publish_at_for_number(number)
        if published_at is None:
            continue
        items.append({
            'date': published_at.astimezone(timezone_name).date(),
            'key': str(number),
            'number': number,
            'anchor': '{}-{}'.format(game_id, number),
            'href': href_for_number(number),
        })
    return items


def prepare_daily_archive(request, links, *, game_id, publish_at_for_number,
                          href_for_number, timezone_name, game_label,
                          number_for_link=None, calendar_id=None):
    """Build the shared archive items and initial month selection for a hub."""
    items = build_daily_archive_items(
        links,
        game_id=game_id,
        publish_at_for_number=publish_at_for_number,
        href_for_number=href_for_number,
        timezone_name=timezone_name,
        number_for_link=number_for_link,
    )
    context = build_daily_archive_context(
        items=items,
        requested_month=request.GET.get('month'),
        today=timezone.localdate(),
        archive_url=request.path,
        game_label=game_label,
        archive_query=archive_query_string(request.GET),
        calendar_id=calendar_id or 'daily-archive-{}'.format(game_id),
    )
    return items, context


def links_for_archive_month(links, selected, *, publish_at_for_number,
                            number_for_link=None, timezone_name=None):
    """Keep links in the selected archive month, preserving their input order."""
    if not selected:
        return list(links)
    selected_links = []
    for link in links:
        number = number_for_link(link) if number_for_link else link.number
        published_at = publish_at_for_number(number)
        if published_at is None:
            continue
        local_date = published_at.astimezone(timezone_name).date() if timezone_name else published_at.date()
        if (local_date.year, local_date.month) == selected:
            selected_links.append(link)
    return selected_links


def refresh_daily_archive_context(request, *, items, game_label, game_id,
                                  completed_keys=(), status_by_key=None,
                                  locked_keys=()):
    """Rebuild archive state after a hub has computed row completion and locks."""
    return build_daily_archive_context(
        items=items,
        requested_month=request.GET.get('month'),
        today=timezone.localdate(),
        archive_url=request.path,
        game_label=game_label,
        archive_query=archive_query_string(request.GET),
        calendar_id='daily-archive-{}'.format(game_id),
        completed_keys=completed_keys,
        status_by_key=status_by_key,
        locked_keys=locked_keys,
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
                                status_by_key=None, locked_keys=(),
                                subscription_url='/subscription/',
                                calendar_id='daily-archive'):
    """Build calendar/navigation context from neutral dated archive items."""
    today = today or date.today()
    completed_keys = {str(key) for key in completed_keys}
    status_by_key = {str(key): value for key, value in (status_by_key or {}).items()}
    locked_keys = {str(key) for key in locked_keys}
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

    def month_label(month_value):
        return '{} {}'.format(MONTH_NAMES[month_value.month], month_value.year)

    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for day in week:
            item = items_by_date.get(day)
            available = item is not None and day.month == month
            completed = available and str(item.get('key')) in completed_keys
            archive_status = status_by_key.get(str(item.get('key'))) if available else None
            if completed:
                archive_status = 'solved'
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
                'is_locked': available and str(item.get('key')) in locked_keys,
                'archive_status': archive_status,
                'is_today': day == today,
                'href': subscription_url if available and str(item.get('key')) in locked_keys else (item.get('href') if available else ''),
                'anchor': item.get('anchor') if available else '',
                'aria_label': label,
            })
        weeks.append(cells)

    return {
        'daily_archive': True,
        'daily_archive_month': month_key(month_date),
        'daily_archive_month_label': '{} {}'.format(MONTH_NAMES[month], year),
        'daily_archive_archive_label': '{} {}'.format(MONTH_NAMES_GENITIVE[month], year),
        'daily_archive_months': [
            {'key': '{}-{:02d}'.format(y, m), 'label': '{} {}'.format(MONTH_NAMES[m], y),
             'href': url_for(date(y, m, 1)), 'is_selected': (y, m) == selected}
            for y, m in months
        ],
        'daily_archive_previous': {
            'label': 'Предыдущий месяц', 'month_label': month_label(previous),
            'href': url_for(previous)
        } if previous else None,
        'daily_archive_next': {
            'label': 'Следующий месяц', 'month_label': month_label(following),
            'href': url_for(following)
        } if following else None,
        'daily_archive_weeks': weeks,
        'daily_archive_url': archive_url,
        'daily_archive_calendar_id': calendar_id,
        'daily_archive_items': [item for item in items if item['date'].year == year and item['date'].month == month],
        'daily_archive_selected': selected,
    }
