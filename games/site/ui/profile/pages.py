"""Compatibility alias. Implementation lives in games.site.pages.profile.pages."""
import sys

from games.site.pages.profile import pages as _implementation

sys.modules[__name__] = _implementation
