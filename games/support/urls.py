"""Compatibility alias. Implementation lives in games.ops.support.http.urls."""
import sys

from games.ops.support.http import urls as _implementation

sys.modules[__name__] = _implementation
