"""Compatibility alias. Implementation lives in games.puzzles.censorly.pool_build.balance."""
import sys

from games.puzzles.censorly.pool_build import balance as _implementation

sys.modules[__name__] = _implementation
