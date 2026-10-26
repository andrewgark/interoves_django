"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.endings."""
import sys

from games.puzzles.censorly.text import endings as _implementation

sys.modules[__name__] = _implementation
