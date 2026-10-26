"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.wordle."""
import sys

from games.puzzles.alphabetty import wordle as _implementation

sys.modules[__name__] = _implementation
