"""Compatibility alias. Implementation lives in games.ops.analytics.identity."""
import sys

from games.ops.analytics import identity as _implementation

sys.modules[__name__] = _implementation
