"""Compatibility alias. Implementation lives in games.ops.workers.jobs.actions."""
import sys

from games.ops.workers.jobs import actions as _implementation

sys.modules[__name__] = _implementation
