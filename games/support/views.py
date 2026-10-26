"""Compatibility alias. Implementation lives in games.ops.support.http.views."""
import sys

from games.ops.support.http import views as _implementation

sys.modules[__name__] = _implementation
