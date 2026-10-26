"""Compatibility alias. Implementation lives in games.ops.telegram.links."""
import sys

from games.ops.telegram import links as _implementation

sys.modules[__name__] = _implementation
