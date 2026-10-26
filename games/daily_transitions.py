"""Compatibility alias. Implementation lives in games.daily.timing.transitions."""
import sys

from games.daily.timing import transitions as _implementation

sys.modules[__name__] = _implementation
