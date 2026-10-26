"""Compatibility alias. Implementation lives in games.site.http.middleware.auth_session_observability."""
import sys

from games.site.http.middleware import auth_session_observability as _implementation

sys.modules[__name__] = _implementation
