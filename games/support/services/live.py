"""Compatibility alias. Implementation lives in games.ops.support.feed.live."""
import sys

from games.ops.support.feed import live as _implementation

sys.modules[__name__] = _implementation
