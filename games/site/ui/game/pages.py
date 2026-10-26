"""Compatibility alias. Implementation lives in games.site.pages.game.pages."""
import sys

from games.site.pages.game import pages as _implementation

sys.modules[__name__] = _implementation
