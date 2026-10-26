"""Compatibility alias. Implementation lives in games.puzzles.replacements.lines.state."""
import sys

from games.puzzles.replacements.lines import state as _implementation

sys.modules[__name__] = _implementation
