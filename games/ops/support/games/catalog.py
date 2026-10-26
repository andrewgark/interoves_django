"""Compatibility alias. Implementation lives in games.console.games.catalog."""
import sys

from games.console.games import catalog as _implementation

sys.modules[__name__] = _implementation
