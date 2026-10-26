"""Compatibility alias. Implementation lives in games.console.feed.live."""
import sys

from games.console.feed import live as _implementation

sys.modules[__name__] = _implementation
