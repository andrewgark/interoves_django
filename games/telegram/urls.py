"""Compatibility alias. Implementation lives in games.ops.telegram.transport.urls."""
import sys

from games.ops.telegram.transport import urls as _implementation

sys.modules[__name__] = _implementation
