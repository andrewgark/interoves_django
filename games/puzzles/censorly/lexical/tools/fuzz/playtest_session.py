"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.fuzz.playtest_session."""
import sys

from games.puzzles.censorly.offline.tools.fuzz import playtest_session as _implementation

sys.modules[__name__] = _implementation
