"""Compatibility alias. Implementation lives in games.site.views.pages.util."""
import sys

from games.site.views.pages import util as _implementation

sys.modules[__name__] = _implementation
