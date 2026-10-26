"""Compatibility alias. Implementation lives in games.puzzles.censorly.article.redact."""
import sys

from games.puzzles.censorly.article import redact as _implementation

sys.modules[__name__] = _implementation
