"""Compatibility alias. Implementation lives in games.site.http.adaptors."""
import sys

from games.site.http import adaptors as _implementation

sys.modules[__name__] = _implementation
