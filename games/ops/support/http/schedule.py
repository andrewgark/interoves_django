"""Compatibility alias. Implementation lives in games.console.http.schedule."""
import sys

from games.console.http import schedule as _implementation

sys.modules[__name__] = _implementation
