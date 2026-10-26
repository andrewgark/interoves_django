"""Compatibility alias. Implementation lives in games.ops.workers.messages."""
import sys

from games.ops.workers import messages as _implementation

sys.modules[__name__] = _implementation
