"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.apply_reviewed_pairs."""
import sys

from games.puzzles.censorly.lexical.tools import apply_reviewed_pairs as _implementation

sys.modules[__name__] = _implementation
