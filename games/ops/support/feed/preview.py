"""Compatibility alias. Implementation lives in games.console.feed.preview."""
import sys

from games.console.feed import preview as _implementation

sys.modules[__name__] = _implementation
