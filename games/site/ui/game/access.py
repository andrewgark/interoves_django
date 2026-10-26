"""Compatibility alias. Implementation lives in games.site.pages.game.access."""
import sys

from games.site.pages.game import access as _implementation

sys.modules[__name__] = _implementation
