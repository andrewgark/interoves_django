"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.decisions."""
import sys

from games.puzzles.censorly.lexical.russian import decisions as _implementation

sys.modules[__name__] = _implementation
