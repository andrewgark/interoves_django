"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.normalize."""
import sys

from games.puzzles.censorly.text import normalize as _implementation

sys.modules[__name__] = _implementation
