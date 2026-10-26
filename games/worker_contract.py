"""Compatibility alias. Implementation lives in games.ops.workers.contract."""
import sys

from games.ops.workers import contract as _implementation

sys.modules[__name__] = _implementation
