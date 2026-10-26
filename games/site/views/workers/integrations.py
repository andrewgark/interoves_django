"""Compatibility alias. Implementation lives in games.ops.workers.handlers.integrations."""
import sys

import games.ops.workers.handlers.integrations as _implementation

sys.modules[__name__] = _implementation
