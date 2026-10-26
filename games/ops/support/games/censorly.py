"""Compatibility alias. Implementation lives in games.console.games.censorly."""
import sys

from games.console.games import censorly as _implementation

sys.modules[__name__] = _implementation
