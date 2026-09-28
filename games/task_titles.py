"""Compatibility facade for task title helpers."""

from games.tasks.titles import (
    NUMBERED_EDITION_GAME_IDS,
    raddle_share_title,
    task_display_name,
    task_group_page_title,
)

__all__ = [
    'NUMBERED_EDITION_GAME_IDS',
    'raddle_share_title',
    'task_display_name',
    'task_group_page_title',
]
