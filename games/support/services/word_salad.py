"""Compatibility alias. Implementation lives in games.ops.support.games.salad."""
import sys

from games.ops.support.games import salad as _implementation

sys.modules[__name__] = _implementation
