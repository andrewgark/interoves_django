"""Proportions view chip pool for task-group UI."""

from games.puzzles.replacements.proportions import build_proportions_chips_for_tasks


def proportions_chips_for_task_group(task_group, tasks, attempts_info_by_task_id) -> list:
    """Build proportions chips and mark solved tasks hidden from the pool."""
    if task_group.view != 'proportions':
        return []
    chips = build_proportions_chips_for_tasks(tasks)
    for c in chips:
        tid = c.get('task_id')
        ai = attempts_info_by_task_id.get(tid) if tid is not None else None
        c['hide_from_pool'] = bool(ai and ai.is_solved())
    return chips
