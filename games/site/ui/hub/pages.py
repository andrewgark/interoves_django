"""Compatibility alias. Implementation lives in games.site.pages.hub.pages."""
import sys

from games.site.pages.hub import pages as _implementation

sys.modules[__name__] = _implementation
