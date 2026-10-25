"""Compatibility alias. Implementation lives in games.site.views.game.page."""
import sys

from games.site.views.game import page as _implementation

sys.modules[__name__] = _implementation
