"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.play."""
import sys

from games.puzzles.alphabetty import play as _implementation

sys.modules[__name__] = _implementation
