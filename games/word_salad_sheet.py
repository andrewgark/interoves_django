"""Compatibility alias. Implementation lives in games.puzzles.salad.sheet."""
import sys

from games.puzzles.salad import sheet as _implementation

sys.modules[__name__] = _implementation
