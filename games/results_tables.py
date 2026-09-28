"""Shared data loading for results tables.

The UI view and background notification code both need the same ordered set of
task groups and visible tasks.  Keeping that query in one place prevents the
two consumers from slowly acquiring different results semantics.
"""

from django.db.models import Prefetch, Q

from games.daily_section import is_scheduled_game, visible_links
from games.models import Task


class ResultsTaskGroupHeader:
    """Header metadata for one task-group block in a results table."""

    __slots__ = ('number', 'name', '_n_tasks')

    def __init__(self, number, name, n_tasks):
        self.number = number
        self.name = name
        self._n_tasks = n_tasks

    def get_n_tasks_for_results(self):
        return self._n_tasks


def load_results_placements_and_tasks(game, task_group_number=None):
    """Return ordered placements, visible tasks, IDs, and table headers."""
    links = list(
        game.task_group_links.select_related('task_group').prefetch_related(
            Prefetch(
                'task_group__tasks',
                queryset=Task.objects.visible().filter(~Q(task_type='text_with_forms')),
                to_attr='result_tasks',
            )
        )
    )
    if is_scheduled_game(game.id):
        placements = visible_links(links, game, reverse=False)
    else:
        placements = sorted(links, key=lambda placement: placement.key_sort())
    if task_group_number is not None:
        placements = [
            placement
            for placement in placements
            if str(placement.number) == str(task_group_number)
        ]

    task_group_to_tasks = {}
    for placement in placements:
        task_group = placement.task_group
        task_group_to_tasks[placement.number] = sorted(
            getattr(task_group, 'result_tasks', []) or [],
            key=lambda task: task.key_sort(),
        )

    tasks_flat = [
        task
        for placement in placements
        for task in task_group_to_tasks[placement.number]
    ]
    task_ids = [task.id for task in tasks_flat]
    task_group_headers = [
        ResultsTaskGroupHeader(
            placement.number,
            placement.name,
            len(task_group_to_tasks[placement.number]),
        )
        for placement in placements
    ]
    return placements, task_group_to_tasks, tasks_flat, task_ids, task_group_headers


def results_table_headers_context(game, task_group_number=None):
    """Return the lightweight context needed to render a results table header."""
    _placements, task_group_to_tasks, _tasks_flat, _task_ids, task_group_headers = (
        load_results_placements_and_tasks(game, task_group_number=task_group_number)
    )
    return {
        'task_groups': task_group_headers,
        'task_group_to_tasks': task_group_to_tasks,
    }
