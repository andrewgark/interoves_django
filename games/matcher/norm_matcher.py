"""Compatibility alias. Implementation lives in games.puzzles.core.norm_matcher."""
import sys

from games.puzzles.core import norm_matcher as _implementation

sys.modules[__name__] = _implementation
