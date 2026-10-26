"""Compatibility alias. Implementation lives in games.ops.workers.config."""
import sys

from games.ops.workers import config as _implementation

sys.modules[__name__] = _implementation
