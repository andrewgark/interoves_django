"""Compatibility alias. Implementation lives in games.ops.support.games.sections."""
import sys

from games.ops.support.games import sections as _implementation

sys.modules[__name__] = _implementation
