"""Compatibility alias. Implementation lives in games.site.views.pages.public.feed."""
import sys

from games.site.views.pages.public import feed as _implementation

sys.modules[__name__] = _implementation
