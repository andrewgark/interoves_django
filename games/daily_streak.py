"""Compatibility alias. Implementation lives in games.daily.timing.streak."""
import sys

from games.daily.timing import streak as _implementation

sys.modules[__name__] = _implementation
