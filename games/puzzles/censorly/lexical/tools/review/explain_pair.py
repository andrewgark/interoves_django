"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.review.explain_pair."""
import sys

from games.puzzles.censorly.offline.tools.review import explain_pair as _implementation

sys.modules[__name__] = _implementation
