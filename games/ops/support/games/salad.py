"""Compatibility alias. Implementation lives in games.console.games.salad."""
import sys

from games.console.games import salad as _implementation

sys.modules[__name__] = _implementation
