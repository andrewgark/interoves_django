"""GameTaskGroup placement lookup for task-group UI."""

from games.models import GameTaskGroup


def resolve_task_group_placement(game, task_group, placement=None):
    """Return explicit placement or load GameTaskGroup row for game + task_group."""
    if placement is not None:
        return placement
    if not (getattr(task_group, 'pk', None) and getattr(game, 'pk', None)):
        return placement
    return (
        GameTaskGroup.objects
        .filter(game_id=game.pk, task_group_id=task_group.pk)
        .only('number', 'name')
        .first()
    )
