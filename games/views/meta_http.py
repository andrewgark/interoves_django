"""Compatibility alias. Implementation lives in games.site.views.pages.meta."""
import sys

from games.site.views.pages import meta as _implementation

sys.modules[__name__] = _implementation
