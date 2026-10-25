"""Compatibility alias. Implementation lives in games.site.views.pages.ui."""
import sys

from games.site.views.pages import ui as _implementation

sys.modules[__name__] = _implementation
