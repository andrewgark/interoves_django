"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.flags."""
import sys

from games.puzzles.censorly.text import flags as _implementation

sys.modules[__name__] = _implementation
