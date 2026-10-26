"""Compatibility alias. Implementation lives in games.realtime.projection."""
import sys

from games.realtime import projection as _implementation

sys.modules[__name__] = _implementation
