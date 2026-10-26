"""Compatibility alias. Implementation lives in games.ops.workers.jobs.salad."""
import sys

from games.ops.workers.jobs import salad as _implementation

sys.modules[__name__] = _implementation
