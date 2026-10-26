"""Compatibility alias. Implementation lives in games.site.pages.play.page."""
import sys

from games.site.pages.play import page as _implementation

sys.modules[__name__] = _implementation
