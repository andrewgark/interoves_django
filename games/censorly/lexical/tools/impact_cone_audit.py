"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.fuzz.impact_cone_audit."""
import sys

from games.puzzles.censorly.lexical.tools.fuzz import impact_cone_audit as _implementation

sys.modules[__name__] = _implementation
