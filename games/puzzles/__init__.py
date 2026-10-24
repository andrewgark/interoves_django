"""Task-type behavior. One subclass per puzzle; shared daily lifecycle stays in games.daily."""

from games.puzzles.alphabetty import AlphabettyPuzzle
from games.puzzles.censorly import CensorlyPuzzle
from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.replacements import ReplacementsPuzzle
from games.puzzles.wall import WallPuzzle
from games.puzzles.word_salad import WordSaladPuzzle

# Puzzles that own attempt-limit and duplicate checks. A duplicate is one of
# three answers: not a duplicate, a duplicate without an attempt id, or a
# duplicate that names the previous attempt.
_BY_TASK_TYPE = {
    RaddlePuzzle.task_type: RaddlePuzzle,
    WordSaladPuzzle.task_type: WordSaladPuzzle,
}


def get_puzzle(task_type):
    """Return the puzzle class that owns check_attempt hooks, or None."""
    if not task_type:
        return None
    return _BY_TASK_TYPE.get(str(task_type))


__all__ = [
    'AlphabettyPuzzle', 'CensorlyPuzzle', 'RaddlePuzzle', 'ReplacementsPuzzle',
    'WallPuzzle', 'WordSaladPuzzle', 'get_puzzle',
]
