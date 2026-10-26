"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.reading_overrides."""
import sys

from games.puzzles.censorly.lexical.russian import reading_overrides as _implementation

sys.modules[__name__] = _implementation
