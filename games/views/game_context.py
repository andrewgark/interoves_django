"""Compatibility alias. Implementation lives in games.site.views.game.context."""
import sys

from games.site.views.game import context as _implementation

sys.modules[__name__] = _implementation
