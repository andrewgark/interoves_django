"""Compatibility alias. Implementation lives in games.site.views.pages.other."""
import sys

from games.site.views.pages import other as _implementation

sys.modules[__name__] = _implementation
