"""Task-type behavior. One subclass per puzzle; shared daily lifecycle stays in games.daily."""

from games.puzzles.raddle import RaddlePuzzle

_BY_TASK_TYPE = {
    RaddlePuzzle.task_type: RaddlePuzzle,
}


def get_puzzle(task_type):
    """Return the puzzle class for a task type, or None when it is not split out yet."""
    if not task_type:
        return None
    return _BY_TASK_TYPE.get(str(task_type))


__all__ = ['RaddlePuzzle', 'get_puzzle']
