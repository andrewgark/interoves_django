"""Task-type behavior. One subclass per puzzle; shared daily lifecycle stays in games.daily."""

from games.puzzles.alphabetty import AlphabettyPuzzle
from games.puzzles.censorly import CensorlyPuzzle
from games.puzzles.html_forms import HtmlFormsPuzzle
from games.puzzles.raddle import RaddlePuzzle
from games.puzzles.replacements import ReplacementsPuzzle
from games.puzzles.wall import WallPuzzle
from games.puzzles.word_salad import WordSaladPuzzle

# Puzzles that own attempt-limit and duplicate checks. A duplicate is one of
# three answers: not a duplicate, a duplicate without an attempt id, or a
# duplicate that names the previous attempt.
_BY_TASK_TYPE = {
    RaddlePuzzle.task_type: RaddlePuzzle,
    ReplacementsPuzzle.task_type: ReplacementsPuzzle,
    WallPuzzle.task_type: WallPuzzle,
    WordSaladPuzzle.task_type: WordSaladPuzzle,
}


def get_puzzle(task_type):
    """Return the puzzle class that owns check_attempt hooks, or None."""
    if not task_type:
        return None
    return _BY_TASK_TYPE.get(str(task_type))


# Posted attempts whose stored text is the whole move. Limit and duplicate
# hooks stay in get_puzzle and are not implied by this set.
_TEXT_MOVE = {
    WallPuzzle.task_type: WallPuzzle,
    ReplacementsPuzzle.task_type: ReplacementsPuzzle,
    HtmlFormsPuzzle.task_type: HtmlFormsPuzzle,
}
POSTED_TEXT_TASK_TYPES = frozenset(_TEXT_MOVE)


def parse_posted_text(task_type, post):
    """Read a wall, replacements, or html-forms POST. None for any other type."""
    puzzle = _TEXT_MOVE.get(task_type)
    if puzzle is None:
        return None
    return puzzle.parse_move(post)


__all__ = [
    'AlphabettyPuzzle', 'CensorlyPuzzle', 'HtmlFormsPuzzle', 'RaddlePuzzle',
    'POSTED_TEXT_TASK_TYPES', 'ReplacementsPuzzle', 'WallPuzzle',
    'WordSaladPuzzle', 'get_puzzle', 'parse_posted_text',
]
