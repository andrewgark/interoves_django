"""Compatibility alias. Implementation lives in games.puzzles.censorly.article.reports."""
import sys

from games.puzzles.censorly.article import reports as _implementation

sys.modules[__name__] = _implementation
