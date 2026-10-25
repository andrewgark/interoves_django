"""Compatibility alias. Implementation lives in games.site.views.game.hint."""
import sys

from games.site.views.game import hint as _implementation

sys.modules[__name__] = _implementation
