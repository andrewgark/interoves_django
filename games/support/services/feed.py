"""Compatibility alias. Implementation lives in games.ops.support.feed.feed."""
import sys

from games.ops.support.feed import feed as _implementation

sys.modules[__name__] = _implementation
