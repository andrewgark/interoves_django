"""Compatibility alias. Implementation lives in games.ops.cron.runtime."""
import sys

from games.ops.cron import runtime as _implementation

sys.modules[__name__] = _implementation
