"""Compatibility alias. Implementation lives in games.realtime.admission."""
import sys

from games.realtime import admission as _implementation

sys.modules[__name__] = _implementation
