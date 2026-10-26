"""Compatibility alias. Implementation lives in games.puzzles.censorly.article.admin."""
import sys

from games.puzzles.censorly.article import admin as _implementation

sys.modules[__name__] = _implementation
