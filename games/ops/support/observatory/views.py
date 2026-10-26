"""Compatibility alias. Implementation lives in games.console.observatory.views."""
import sys

from games.console.observatory import views as _implementation

sys.modules[__name__] = _implementation
