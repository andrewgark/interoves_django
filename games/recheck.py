"""Compatibility alias. Implementation lives in games.ops.workers.jobs.recheck."""
import sys

from games.ops.workers.jobs import recheck as _implementation

sys.modules[__name__] = _implementation
