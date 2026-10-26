"""Compatibility alias. Implementation lives in games.site.pages.results.pages."""
import sys

from games.site.pages.results import pages as _implementation

sys.modules[__name__] = _implementation
