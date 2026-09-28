"""Canonical user-facing titles for task groups and individual tasks."""

import html
import re

from django.utils.html import strip_tags

NUMBERED_EDITION_GAME_IDS = frozenset({'ladder', 'alphabetty', 'week_task', 'salad'})
_RUSSIAN_MONTHS_GENITIVE = (
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def _plain_text(value) -> str:
    text = html.unescape(strip_tags(str(value or '')))
    return ' '.join(text.split())


def raddle_share_title(game, task_group_number, task_number) -> str:
    project_id = str(getattr(game, 'id', '') or getattr(game, 'pk', '') or '')
    group_number = str(task_group_number or '').strip()
    task_number = str(task_number or '').strip()
    match = re.match(r'^des(\d+)$', project_id, re.IGNORECASE)
    if match:
        return 'Лесенка {}.{}.{}'.format(match.group(1), group_number, task_number)
    return 'Лесенка {}.{}'.format(group_number, task_number)


def task_group_page_title(game, placement, *, include_date=False) -> str:
    game_title = game.outside_name or game.name or game.pk
    if str(game.pk) in NUMBERED_EDITION_GAME_IDS:
        title = '{} №{}'.format(game_title, placement.number)
        if include_date:
            from games.daily_section import MOSCOW, publish_at_for
            published_at = publish_at_for(game, placement.number)
            if published_at is not None:
                published_date = published_at.astimezone(MOSCOW).date()
                title += ' {} · {} {} {}'.format(
                    game_title, published_date.day,
                    _RUSSIAN_MONTHS_GENITIVE[published_date.month],
                    published_date.year,
                )
        return title
    return '{} · {}'.format(game_title, placement.name)


def task_display_name(game, task, *, placement=None) -> str:
    if placement is None and getattr(task, 'task_group_id', None):
        from games.models import GameTaskGroup
        placement = GameTaskGroup.objects.filter(
            game_id=game.pk, task_group_id=task.task_group_id,
        ).only('number', 'name').first()
    task_number = getattr(task, 'number', None) or getattr(task, 'pk', '')
    if placement is None:
        return '#{}'.format(task_number)
    if str(game.pk) in NUMBERED_EDITION_GAME_IDS:
        return _plain_text(task_group_page_title(game, placement))
    group_name = _plain_text(placement.name)
    group_label = '{}.'.format(placement.number)
    if group_name:
        group_label += ' {}'.format(group_name)
    return '{} ({})'.format(group_label, task_number)
