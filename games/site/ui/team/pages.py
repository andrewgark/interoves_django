"""Compatibility alias. Implementation lives in games.site.pages.team.pages."""
import sys

from games.site.pages.team import pages as _implementation

sys.modules[__name__] = _implementation
