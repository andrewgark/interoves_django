"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.tools.candidates.article_candidates."""
import sys

from games.puzzles.censorly.offline.tools.candidates import article_candidates as _implementation

sys.modules[__name__] = _implementation
