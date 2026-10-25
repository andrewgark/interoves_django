"""Compatibility alias. Implementation lives in games.site.views.workers.background."""
import sys

from games.site.views.workers import background as _implementation

sys.modules[__name__] = _implementation
