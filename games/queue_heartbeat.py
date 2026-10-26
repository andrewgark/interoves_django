"""Compatibility alias. Implementation lives in games.ops.workers.heartbeat."""
import sys

from games.ops.workers import heartbeat as _implementation

sys.modules[__name__] = _implementation
