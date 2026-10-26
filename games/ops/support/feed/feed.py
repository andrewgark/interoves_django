"""Compatibility alias. Implementation lives in games.console.feed.feed."""
import sys

from games.console.feed import feed as _implementation

sys.modules[__name__] = _implementation
