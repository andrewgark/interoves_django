"""Compatibility alias. Implementation lives in games.console.feed.chain."""
import sys

from games.console.feed import chain as _implementation

sys.modules[__name__] = _implementation
