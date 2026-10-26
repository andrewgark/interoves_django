"""Compatibility alias. Implementation lives in games.ops.telegram.transport.views."""
import sys

from games.ops.telegram.transport import views as _implementation

sys.modules[__name__] = _implementation
