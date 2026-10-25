"""Compatibility alias. Implementation lives in games.site.views.game.raddle."""
import sys

from games.site.views.game import raddle as _implementation

sys.modules[__name__] = _implementation
