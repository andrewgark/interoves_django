"""Compatibility alias. Implementation lives in games.ops.cron.lock."""
import sys

from games.ops.cron import lock as _implementation

sys.modules[__name__] = _implementation
