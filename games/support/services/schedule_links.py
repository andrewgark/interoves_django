"""Compatibility alias. Implementation lives in games.ops.support.http.schedule."""
import sys

from games.ops.support.http import schedule as _implementation

sys.modules[__name__] = _implementation
