"""Compatibility alias. Implementation lives in games.site.pages.play.context."""
import sys

from games.site.pages.play import context as _implementation

sys.modules[__name__] = _implementation
