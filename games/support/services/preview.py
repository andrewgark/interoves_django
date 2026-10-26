"""Compatibility alias. Implementation lives in games.ops.support.feed.preview."""
import sys

from games.ops.support.feed import preview as _implementation

sys.modules[__name__] = _implementation
