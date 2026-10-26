"""Compatibility alias. Implementation lives in games.puzzles.censorly.random_game."""
import sys

from games.puzzles.censorly import random_game as _implementation

sys.modules[__name__] = _implementation
