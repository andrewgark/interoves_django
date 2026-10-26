"""Compatibility alias. Implementation lives in games.site.http.auth_observability."""
import sys

from games.site.http import auth_observability as _implementation

sys.modules[__name__] = _implementation
