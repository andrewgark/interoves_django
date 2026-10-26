"""Compatibility alias. Implementation lives in games.ops.support.games.week."""
import sys

from games.ops.support.games import week as _implementation

sys.modules[__name__] = _implementation
