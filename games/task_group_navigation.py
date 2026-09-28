"""Compatibility facade for task-group navigation helpers."""

from games.tasks.navigation import (
    neighbors_by_pk,
    play_url_for_task_group,
    replay_exit_url_for_task_group,
    replay_url_for_task_group,
    results_url_for_task_group,
    task_group_page_nav_context,
)

__all__ = [
    'neighbors_by_pk',
    'play_url_for_task_group',
    'replay_exit_url_for_task_group',
    'replay_url_for_task_group',
    'results_url_for_task_group',
    'task_group_page_nav_context',
]
