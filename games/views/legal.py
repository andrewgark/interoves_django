"""Compatibility alias. Implementation lives in games.site.views.pages.public.legal."""
import sys

from games.site.views.pages.public import legal as _implementation

sys.modules[__name__] = _implementation
