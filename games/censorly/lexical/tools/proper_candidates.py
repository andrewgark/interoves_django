"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.candidates.proper_candidates."""
import sys

from games.puzzles.censorly.lexical.tools.candidates import proper_candidates as _implementation

sys.modules[__name__] = _implementation
