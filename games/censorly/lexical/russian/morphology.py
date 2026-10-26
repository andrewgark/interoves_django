"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.morphology."""
import sys

from games.puzzles.censorly.lexical.russian import morphology as _implementation

sys.modules[__name__] = _implementation
