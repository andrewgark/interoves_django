"""Compatibility alias. Implementation lives in games.daily.timing.statistics."""
import sys

from games.daily.timing import statistics as _implementation

sys.modules[__name__] = _implementation
