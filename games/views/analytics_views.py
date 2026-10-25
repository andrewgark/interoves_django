"""Compatibility alias. Implementation lives in games.site.views.pages.public.analytics."""
import sys

from games.site.views.pages.public import analytics as _implementation

sys.modules[__name__] = _implementation
