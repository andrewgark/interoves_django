"""Compatibility alias. Implementation lives in games.site.views.results.pages."""
import sys

from games.site.views.results import pages as _implementation

sys.modules[__name__] = _implementation
