"""Compatibility alias. Implementation lives in games.site.views.pages.public.health."""
import sys

from games.site.views.pages.public import health as _implementation

sys.modules[__name__] = _implementation
