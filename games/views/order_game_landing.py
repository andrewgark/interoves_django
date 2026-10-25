"""Compatibility alias. Implementation lives in games.site.views.pages.public.order."""
import sys

from games.site.views.pages.public import order as _implementation

sys.modules[__name__] = _implementation
