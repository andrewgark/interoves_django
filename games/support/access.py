"""Compatibility alias. Implementation lives in games.ops.support.http.access."""
import sys

from games.ops.support.http import access as _implementation

sys.modules[__name__] = _implementation
