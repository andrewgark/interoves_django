"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.fuzz.impact_cone_audit."""
import sys

from games.puzzles.censorly.offline.tools.fuzz import impact_cone_audit as _implementation

sys.modules[__name__] = _implementation
