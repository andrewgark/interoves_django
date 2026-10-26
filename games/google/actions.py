"""Compatibility alias. Implementation lives in games.ops.support.google.actions."""
import sys

from games.ops.support.google import actions as _implementation

sys.modules[__name__] = _implementation
