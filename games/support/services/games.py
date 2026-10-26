"""Compatibility alias. Implementation lives in games.ops.support.games.catalog."""
import sys

from games.ops.support.games import catalog as _implementation

sys.modules[__name__] = _implementation
