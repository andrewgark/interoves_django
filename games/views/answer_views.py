"""Compatibility alias. Implementation lives in games.site.views.game.answer."""
import sys

from games.site.views.game import answer as _implementation

sys.modules[__name__] = _implementation
