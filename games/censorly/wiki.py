"""Compatibility alias. Implementation lives in games.puzzles.censorly.article.wiki."""
import sys

from games.puzzles.censorly.article import wiki as _implementation

sys.modules[__name__] = _implementation
