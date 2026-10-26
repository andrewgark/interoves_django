"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.fuzz.cold_start_profile."""
import sys

from games.puzzles.censorly.offline.tools.fuzz import cold_start_profile as _implementation

sys.modules[__name__] = _implementation
