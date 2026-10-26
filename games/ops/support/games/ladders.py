"""Compatibility alias. Implementation lives in games.console.games.ladder."""
import sys

from games.console.games import ladder as _implementation

sys.modules[__name__] = _implementation
