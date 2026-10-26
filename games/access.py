"""Compatibility alias. Implementation lives in games.club.access.game."""
import sys

from games.club.access import game as _implementation

sys.modules[__name__] = _implementation
