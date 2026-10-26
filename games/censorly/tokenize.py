"""Compatibility alias. Implementation lives in games.puzzles.censorly.text.tokenize."""
import sys

from games.puzzles.censorly.text import tokenize as _implementation

sys.modules[__name__] = _implementation
