"""Compatibility alias. Implementation lives in games.console.games.sections."""
import sys

from games.console.games import sections as _implementation

sys.modules[__name__] = _implementation
