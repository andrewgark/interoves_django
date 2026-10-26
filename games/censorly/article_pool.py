"""Compatibility alias. Implementation lives in games.puzzles.censorly.article.pool."""
import sys

from games.puzzles.censorly.article import pool as _implementation

sys.modules[__name__] = _implementation
