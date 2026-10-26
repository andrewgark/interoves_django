"""Compatibility alias. Implementation lives in games.console.feed.stats."""
import sys

from games.console.feed import stats as _implementation

sys.modules[__name__] = _implementation
