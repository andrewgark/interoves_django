"""Compatibility alias. Implementation lives in games.puzzles.censorly.pool_build.filters."""
import sys

from games.puzzles.censorly.pool_build import filters as _implementation

sys.modules[__name__] = _implementation
