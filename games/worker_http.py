"""Compatibility alias. Implementation lives in games.ops.workers.http."""
import sys

from games.ops.workers import http as _implementation

sys.modules[__name__] = _implementation
