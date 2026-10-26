"""Compatibility alias. Implementation lives in games.ops.support.feed.stats."""
import sys

from games.ops.support.feed import stats as _implementation

sys.modules[__name__] = _implementation
