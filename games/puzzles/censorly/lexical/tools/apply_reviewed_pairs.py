"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.apply_reviewed_pairs."""
import sys

from games.puzzles.censorly.offline.tools import apply_reviewed_pairs as _implementation

sys.modules[__name__] = _implementation
