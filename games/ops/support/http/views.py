"""Compatibility alias. Implementation lives in games.console.http.views."""
import sys

from games.console.http import views as _implementation

sys.modules[__name__] = _implementation
