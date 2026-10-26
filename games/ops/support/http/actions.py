"""Compatibility alias. Implementation lives in games.console.http.actions."""
import sys

from games.console.http import actions as _implementation

sys.modules[__name__] = _implementation
