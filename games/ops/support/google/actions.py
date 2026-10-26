"""Compatibility alias. Implementation lives in games.console.google.actions."""
import sys

from games.console.google import actions as _implementation

sys.modules[__name__] = _implementation
