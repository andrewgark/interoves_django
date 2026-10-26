"""Compatibility alias. Implementation lives in games.ops.telegram.transport.api."""
import sys

from games.ops.telegram.transport import api as _implementation

sys.modules[__name__] = _implementation
