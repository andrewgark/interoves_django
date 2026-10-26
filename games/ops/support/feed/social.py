"""Compatibility alias. Implementation lives in games.console.feed.social."""
import sys

from games.console.feed import social as _implementation

sys.modules[__name__] = _implementation
