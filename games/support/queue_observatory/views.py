"""Compatibility alias. Implementation lives in games.ops.support.observatory.views."""
import sys

from games.ops.support.observatory import views as _implementation

sys.modules[__name__] = _implementation
