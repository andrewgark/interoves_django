"""Compatibility alias. Implementation lives in games.site.http.middleware.anonymous_identity."""
import sys

from games.site.http.middleware import anonymous_identity as _implementation

sys.modules[__name__] = _implementation
