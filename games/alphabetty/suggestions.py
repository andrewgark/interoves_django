"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.suggestions."""
import sys

from games.puzzles.alphabetty import suggestions as _implementation

sys.modules[__name__] = _implementation
