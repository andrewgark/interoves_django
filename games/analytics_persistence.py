"""Compatibility alias. Implementation lives in games.ops.analytics.persistence."""
import sys

from games.ops.analytics import persistence as _implementation

sys.modules[__name__] = _implementation
