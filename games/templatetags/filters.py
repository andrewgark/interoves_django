"""Compatibility alias. Implementation lives in games.site.http.tags.filters."""
import sys

from games.site.http.tags import filters as _implementation

sys.modules[__name__] = _implementation
