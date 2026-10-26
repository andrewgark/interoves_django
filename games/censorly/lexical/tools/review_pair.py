"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.review_pair."""
import sys

from games.puzzles.censorly.lexical.tools import review_pair as _implementation

sys.modules[__name__] = _implementation
