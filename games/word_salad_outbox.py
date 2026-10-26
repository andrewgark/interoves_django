"""Compatibility alias. Implementation lives in games.ops.workers.jobs.outbox."""
import sys

from games.ops.workers.jobs import outbox as _implementation

sys.modules[__name__] = _implementation
