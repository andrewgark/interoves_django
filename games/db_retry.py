"""Compatibility alias. Implementation lives in games.realtime.db."""
import sys

from games.realtime import db as _implementation

sys.modules[__name__] = _implementation
