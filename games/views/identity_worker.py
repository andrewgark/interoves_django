"""Compatibility alias. Implementation lives in games.site.views.workers.identity."""
import sys

from games.site.views.workers import identity as _implementation

sys.modules[__name__] = _implementation
