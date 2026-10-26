"""Compatibility alias. Implementation lives in games.site.pages.play.live."""
import sys

from games.site.pages.play import live as _implementation

sys.modules[__name__] = _implementation
