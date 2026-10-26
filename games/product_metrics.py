"""Compatibility alias. Implementation lives in games.ops.analytics.metrics."""
import sys

from games.ops.analytics import metrics as _implementation

sys.modules[__name__] = _implementation
