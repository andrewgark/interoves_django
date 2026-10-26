"""Task-type behavior. One subclass per puzzle; shared daily lifecycle stays in games.daily."""

from __future__ import annotations

import importlib
from typing import Any

# Task types whose POST body is the whole move (see parse_posted_text).
POSTED_TEXT_TASK_TYPES = frozenset({'wall', 'replacements_lines', 'html_forms'})

_BY_TASK_TYPE: dict[str, tuple[str, str]] = {
    'html_forms': ('games.puzzles.replacements.html_forms', 'HtmlFormsPuzzle'),
    'raddle': ('games.puzzles.raddle.puzzle', 'RaddlePuzzle'),
    'replacements_lines': ('games.puzzles.replacements.puzzle', 'ReplacementsPuzzle'),
    'wall': ('games.puzzles.wall.puzzle', 'WallPuzzle'),
    'word_salad': ('games.puzzles.salad.puzzle', 'WordSaladPuzzle'),
}

_puzzle_class_cache: dict[str, type] = {}

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    'AlphabettyPuzzle': ('games.puzzles.alphabetty.play', 'AlphabettyPuzzle'),
    'AlphabettyMove': ('games.puzzles.alphabetty.play', 'AlphabettyMove'),
    'AlphabettyMoveRefusal': ('games.puzzles.alphabetty.play', 'AlphabettyMoveRefusal'),
    'CensorlyPuzzle': ('games.puzzles.censorly.play', 'CensorlyPuzzle'),
    'CensorlyMoveRefusal': ('games.puzzles.censorly.play', 'CensorlyMoveRefusal'),
    'HtmlFormsPuzzle': ('games.puzzles.replacements.html_forms', 'HtmlFormsPuzzle'),
    'RaddlePuzzle': ('games.puzzles.raddle.puzzle', 'RaddlePuzzle'),
    'ReplacementsPuzzle': ('games.puzzles.replacements.puzzle', 'ReplacementsPuzzle'),
    'WallPuzzle': ('games.puzzles.wall.puzzle', 'WallPuzzle'),
    'WordSaladPuzzle': ('games.puzzles.salad.puzzle', 'WordSaladPuzzle'),
}


def _load_symbol(module_path: str, attr: str) -> Any:
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def _puzzle_class(task_type: str) -> type | None:
    spec = _BY_TASK_TYPE.get(str(task_type))
    if spec is None:
        return None
    if task_type not in _puzzle_class_cache:
        module_path, attr = spec
        _puzzle_class_cache[task_type] = _load_symbol(module_path, attr)
    return _puzzle_class_cache[task_type]


def get_puzzle(task_type):
    """Return the puzzle class that owns check_attempt hooks, or None."""
    if not task_type:
        return None
    return _puzzle_class(str(task_type))


def parse_posted_text(task_type, post):
    """Read a wall, replacements, or html-forms POST. None for any other type."""
    if task_type not in POSTED_TEXT_TASK_TYPES:
        return None
    puzzle = _puzzle_class(str(task_type))
    if puzzle is None:
        return None
    return puzzle.parse_move(post)


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module_path, attr = spec
    return _load_symbol(module_path, attr)


__all__ = [
    'AlphabettyMove',
    'AlphabettyMoveRefusal',
    'AlphabettyPuzzle',
    'CensorlyMoveRefusal',
    'CensorlyPuzzle',
    'HtmlFormsPuzzle',
    'POSTED_TEXT_TASK_TYPES',
    'RaddlePuzzle',
    'ReplacementsPuzzle',
    'WallPuzzle',
    'WordSaladPuzzle',
    'get_puzzle',
    'parse_posted_text',
]
