"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.tools.review.tier_review."""
import sys

from games.puzzles.censorly.lexical.tools.review import tier_review as _implementation

sys.modules[__name__] = _implementation
