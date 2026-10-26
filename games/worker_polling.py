"""Compatibility alias. Implementation lives in games.ops.workers.polling."""
import sys

from games.ops.workers import polling as _implementation

sys.modules[__name__] = _implementation
