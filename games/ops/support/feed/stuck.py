"""Compatibility alias. Implementation lives in games.console.feed.stuck."""
import sys

from games.console.feed import stuck as _implementation

sys.modules[__name__] = _implementation
