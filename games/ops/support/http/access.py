"""Compatibility alias. Implementation lives in games.console.http.access."""
import sys

from games.console.http import access as _implementation

sys.modules[__name__] = _implementation
