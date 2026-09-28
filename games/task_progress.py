"""Actor progress calculations shared by game UI surfaces."""

from games.models import Task


def compute_task_progress(
    game,
    task_groups,
    team=None,
    user=None,
    anon_key=None,
    mode='general',
    *,
    include_other_games=False,
):
    """Return solved tasks, task-group membership, and actor points.

    ``include_other_games`` is used by training sections where the same
    canonical task group can be referenced by multiple games.
    """
    from games.scoring import Actor, bulk_actor_task_progress

    tg_ids = [tg.id for tg in task_groups]
    tasks = list(Task.objects.filter(task_group_id__in=tg_ids).visible())

    solved_task_ids = set()
    task_result_points = {}
    if tasks:
        actor = None
        if team is not None:
            actor = Actor(team_id=team.pk)
        elif user is not None:
            actor = Actor(user_id=user.pk)
        elif anon_key is not None:
            actor = Actor(anon_key=str(anon_key))
        if actor is not None:
            solved_task_ids, progress_by_task_id = bulk_actor_task_progress(
                tasks=tasks,
                actor=actor,
                mode=mode,
                game=game,
                include_other_games=include_other_games,
            )
            task_result_points = {
                task_id: progress[0]
                for task_id, progress in progress_by_task_id.items()
            }

    tg_to_task_ids = {}
    for task in tasks:
        tg_to_task_ids.setdefault(task.task_group_id, []).append(task.id)

    return solved_task_ids, tg_to_task_ids, task_result_points


def merge_task_group_progress_rows(task_group_rows, progress_rows):
    """Apply an actor projection to already-built task-group display rows."""
    for row in task_group_rows:
        progress = progress_rows.get(str(row.get('number')))
        if not progress:
            continue
        for key in (
            'n_solved',
            'n_tasks',
            'is_fully_solved',
            'row_class',
            'progress_text',
            'result_squares',
            'elapsed_label',
        ):
            if key in progress:
                row[key] = progress[key]
    return task_group_rows
