"""Compatibility alias. Implementation lives in games.club.next_game."""
import sys

from games.club import next_game as _implementation

sys.modules[__name__] = _implementation
