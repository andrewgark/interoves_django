"""Compatibility alias. Implementation lives in games.ops.support.games.alphabetty."""
import sys

from games.ops.support.games import alphabetty as _implementation

sys.modules[__name__] = _implementation
