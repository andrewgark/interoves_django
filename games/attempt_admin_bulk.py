"""Compatibility alias. Implementation lives in games.ops.workers.jobs.bulk."""
import sys

from games.ops.workers.jobs import bulk as _implementation

sys.modules[__name__] = _implementation
