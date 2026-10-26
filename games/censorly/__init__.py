"""Цензурки: Redactle-style daily Wikipedia guessing game."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parent.parent / 'puzzles' / 'censorly' / 'ids.py'
_spec = importlib.util.spec_from_file_location('games.puzzles.censorly.ids', _path)
_ids = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_ids)

CENSORLY_GAME_ID = _ids.CENSORLY_GAME_ID
CENSORLY_TASK_TYPE = _ids.CENSORLY_TASK_TYPE
CENSORLY_CHECKER_ID = _ids.CENSORLY_CHECKER_ID
CENSORLY_TAGS_KEY = _ids.CENSORLY_TAGS_KEY
CENSORLY_SHOW_MASK_ENDINGS = _ids.CENSORLY_SHOW_MASK_ENDINGS
CENSORLY_SPLITTER_VERSION = _ids.CENSORLY_SPLITTER_VERSION

__all__ = [
    'CENSORLY_CHECKER_ID',
    'CENSORLY_GAME_ID',
    'CENSORLY_SHOW_MASK_ENDINGS',
    'CENSORLY_SPLITTER_VERSION',
    'CENSORLY_TAGS_KEY',
    'CENSORLY_TASK_TYPE',
]
