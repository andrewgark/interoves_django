"""Compatibility alias. Implementation lives in games.puzzles.raddle.concurrency."""
import sys

from games.puzzles.raddle import concurrency as _implementation

sys.modules[__name__] = _implementation
