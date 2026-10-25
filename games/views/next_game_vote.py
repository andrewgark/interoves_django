"""Compatibility alias. Implementation lives in games.site.views.pages.public.vote."""
import sys

from games.site.views.pages.public import vote as _implementation

sys.modules[__name__] = _implementation
