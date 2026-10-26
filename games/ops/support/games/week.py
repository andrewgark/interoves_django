"""Compatibility alias. Implementation lives in games.console.games.week."""
import sys

from games.console.games import week as _implementation

sys.modules[__name__] = _implementation
