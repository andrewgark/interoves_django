"""Compatibility alias. Implementation lives in games.ops.workers.handlers.identity."""
import sys

import games.ops.workers.handlers.identity as _implementation

sys.modules[__name__] = _implementation
