"""Compatibility alias. Implementation lives in games.site.views.game.render."""
import sys

from games.site.views.game import render as _implementation

sys.modules[__name__] = _implementation
