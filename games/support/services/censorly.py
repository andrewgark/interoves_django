"""Compatibility alias. Implementation lives in games.ops.support.games.censorly."""
import sys

from games.ops.support.games import censorly as _implementation

sys.modules[__name__] = _implementation
