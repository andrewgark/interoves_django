"""Compatibility alias. Implementation lives in games.site.http.exceptions."""
import sys

from games.site.http import exceptions as _implementation

sys.modules[__name__] = _implementation
