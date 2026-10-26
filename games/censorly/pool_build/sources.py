"""Compatibility alias. Implementation lives in games.puzzles.censorly.pool_build.sources."""
import sys

from games.puzzles.censorly.pool_build import sources as _implementation

sys.modules[__name__] = _implementation
