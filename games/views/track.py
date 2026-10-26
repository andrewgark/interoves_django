"""Compatibility alias. Implementation lives in games.realtime.track_compat."""
import sys

from games.realtime import track_compat as _implementation

sys.modules[__name__] = _implementation
