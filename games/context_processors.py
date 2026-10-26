"""Compatibility alias. Implementation lives in games.site.http.context."""
import sys

from games.site.http import context as _implementation

sys.modules[__name__] = _implementation
