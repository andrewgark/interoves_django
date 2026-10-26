"""Compatibility alias. Implementation lives in games.site.http.middleware.db_transient_response."""
import sys

from games.site.http.middleware import db_transient_response as _implementation

sys.modules[__name__] = _implementation
