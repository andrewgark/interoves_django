"""Compatibility alias. Implementation lives in games.site.http.signals."""
import sys

from games.site.http import signals as _implementation

sys.modules[__name__] = _implementation
