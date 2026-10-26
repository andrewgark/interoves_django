"""Compatibility alias. Implementation lives in games.puzzles.replacements.proportions."""
import sys

from games.puzzles.replacements import proportions as _implementation

sys.modules[__name__] = _implementation
