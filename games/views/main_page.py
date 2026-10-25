"""Compatibility alias. Implementation lives in games.site.views.pages.public.main."""
import sys

from games.site.views.pages.public import main as _implementation

sys.modules[__name__] = _implementation
