"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.roots."""
import sys

from games.puzzles.censorly.text import roots as _implementation

sys.modules[__name__] = _implementation
