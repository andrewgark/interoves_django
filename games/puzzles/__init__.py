"""Task-type behavior. One subclass per puzzle; shared daily lifecycle stays in games.daily."""

from games.puzzles.alphabetty import AlphabettyPuzzle
from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.word_salad import WordSaladPuzzle

# Only puzzles that own attempt-limit and duplicate checks. Salad still uses
# the generic text duplicate, which records the previous attempt id.
_BY_TASK_TYPE = {
    RaddlePuzzle.task_type: RaddlePuzzle,
}


def get_puzzle(task_type):
    """Return the puzzle class that owns check_attempt hooks, or None."""
    if not task_type:
        return None
    return _BY_TASK_TYPE.get(str(task_type))


__all__ = ['AlphabettyPuzzle', 'RaddlePuzzle', 'WordSaladPuzzle', 'get_puzzle']
