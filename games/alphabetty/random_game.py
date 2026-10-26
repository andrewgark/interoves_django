"""Compatibility alias. Implementation lives in games.puzzles.alphabetty.random_game."""
import sys

from games.puzzles.alphabetty import random_game as _implementation

sys.modules[__name__] = _implementation
