"""Compatibility alias. Implementation lives in games.ops.support.feed.stuck."""
import sys

from games.ops.support.feed import stuck as _implementation

sys.modules[__name__] = _implementation
