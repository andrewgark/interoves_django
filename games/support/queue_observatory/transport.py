"""Compatibility alias. Implementation lives in games.ops.support.observatory.transport."""
import sys

from games.ops.support.observatory import transport as _implementation

sys.modules[__name__] = _implementation
