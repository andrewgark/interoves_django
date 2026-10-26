"""Compatibility alias. Implementation lives in games.ops.support.http.actions."""
import sys

from games.ops.support.http import actions as _implementation

sys.modules[__name__] = _implementation
