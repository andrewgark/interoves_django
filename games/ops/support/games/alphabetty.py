"""Compatibility alias. Implementation lives in games.console.games.alphabetty."""
import sys

from games.console.games import alphabetty as _implementation

sys.modules[__name__] = _implementation
