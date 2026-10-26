"""Compatibility alias. Implementation lives in games.puzzles.raddle.contract."""
import sys

from games.puzzles.raddle import contract as _implementation

sys.modules[__name__] = _implementation
