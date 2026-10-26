"""Compatibility alias. Implementation lives in games.console.http.urls."""
import sys

from games.console.http import urls as _implementation

sys.modules[__name__] = _implementation
