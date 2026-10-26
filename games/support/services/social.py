"""Compatibility alias. Implementation lives in games.ops.support.feed.social."""
import sys

from games.ops.support.feed import social as _implementation

sys.modules[__name__] = _implementation
