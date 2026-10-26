"""Compatibility alias. Implementation lives in games.ops.workers.handlers.background."""
import sys

import games.ops.workers.handlers.background as _implementation

sys.modules[__name__] = _implementation
