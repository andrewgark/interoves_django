"""Compatibility alias. Implementation lives in games.site.http.middleware.request_timing."""
import sys

from games.site.http.middleware import request_timing as _implementation

sys.modules[__name__] = _implementation
