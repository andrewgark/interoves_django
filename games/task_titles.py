"""Canonical user-facing titles for task groups and individual tasks."""

import html

from django.utils.html import strip_tags


# These sections identify an issue by its published edition, just like the page
# heading does.  The task's internal number is not useful there (usually "1").
NUMBERED_EDITION_GAME_IDS = frozenset({'ladder', 'alphabetty', 'week_task', 'word_salad'})


def _plain_text(value) -> str:
    """Turn a possibly-HTML display name into compact plain text."""
    text = html.unescape(strip_tags(str(value or '')))
    return ' '.join(text.split())


def task_group_page_title(game, placement) -> str:
    """Return the canonical heading for a task-group page."""
    game_title = game.outside_name or game.name or game.pk
    if str(game.pk) in NUMBERED_EDITION_GAME_IDS:
        return '{} №{}'.format(game_title, placement.number)
    return '{} · {}'.format(game_title, placement.name)


def task_display_name(game, task, *, placement=None) -> str:
    """
    Return a task name that identifies it in the context of ``game``.

    Numbered recurring sections use the same title as their page (for example,
    ``Лесенка №47``).  Other games include the task-group number and name,
    followed by the task number.  If the task is no longer placed in the game,
    retain the old ``#<number>`` fallback.
    """
    if placement is None and getattr(task, 'task_group_id', None):
        from games.models import GameTaskGroup

        placement = (
            GameTaskGroup.objects
            .filter(game_id=game.pk, task_group_id=task.task_group_id)
            .only('number', 'name')
            .first()
        )

    task_number = getattr(task, 'number', None) or getattr(task, 'pk', '')
    if placement is None:
        return '#{}'.format(task_number)

    if str(game.pk) in NUMBERED_EDITION_GAME_IDS:
        return _plain_text(task_group_page_title(game, placement))

    group_name = _plain_text(placement.name)
    group_label = '{}.'.format(placement.number)
    if group_name:
        group_label += ' {}'.format(group_name)
    return '{} · задание {}'.format(group_label, task_number)
