"""Compatibility alias. Implementation lives in games.ops.support.feed.chain."""
import sys

from games.ops.support.feed import chain as _implementation

sys.modules[__name__] = _implementation
