"""Compatibility alias. Implementation lives in games.console.observatory.transport."""
import sys

from games.console.observatory import transport as _implementation

sys.modules[__name__] = _implementation
