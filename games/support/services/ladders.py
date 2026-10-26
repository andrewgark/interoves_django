"""Compatibility alias. Implementation lives in games.ops.support.games.ladders."""
import sys

from games.ops.support.games import ladders as _implementation

sys.modules[__name__] = _implementation
