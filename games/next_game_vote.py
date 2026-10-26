"""Compatibility alias. Implementation lives in games.club.vote."""
import sys

from games.club import vote as _implementation

sys.modules[__name__] = _implementation
