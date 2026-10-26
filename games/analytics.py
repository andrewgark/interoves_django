"""Compatibility alias. Implementation lives in games.ops.analytics.events."""
import sys

from games.ops.analytics import events as _implementation

sys.modules[__name__] = _implementation
