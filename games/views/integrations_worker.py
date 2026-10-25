"""Compatibility alias. Implementation lives in games.site.views.workers.integrations."""
import sys

from games.site.views.workers import integrations as _implementation

sys.modules[__name__] = _implementation
