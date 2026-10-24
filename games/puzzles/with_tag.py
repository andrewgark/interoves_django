"""Метка: one answer that rechecks another task. The view still rechecks and notifies."""

from __future__ import annotations

from games.puzzles.base import Puzzle


def dependent_actor(task):
    """The task and team a tagged answer must recheck. None when the tag cannot be resolved."""
    tag_task_number = task.tags.get('task')
    tag_team_name = task.tags.get('team')
    if tag_task_number is None or tag_team_name is None:
        return None
    try:
        from games.models import Task, Team
        tag_task = Task.objects.visible().get(
            task_group=task.task_group, checker_data__contains=tag_task_number,
        )
        tag_team = Team.objects.get(name=tag_team_name)
        assert tag_task.task_type != 'with_tag'
    except Exception:
        return None
    return tag_task, tag_team


class WithTagPuzzle(Puzzle):
    task_type = 'with_tag'

    dependent_actor = staticmethod(dependent_actor)
