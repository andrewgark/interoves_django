"""Compatibility alias. Implementation lives in games.puzzles.censorly.play."""
import sys

from games.puzzles.censorly import play as _implementation

sys.modules[__name__] = _implementation
