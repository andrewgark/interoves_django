"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.match."""
import sys

from games.puzzles.censorly.lexical import match as _implementation

sys.modules[__name__] = _implementation
