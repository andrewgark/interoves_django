"""Compatibility alias. Implementation lives in games.site.views.results.total."""
import sys

from games.site.views.results import total as _implementation

sys.modules[__name__] = _implementation
