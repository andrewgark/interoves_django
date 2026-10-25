"""Compatibility alias. Implementation lives in games.site.views.game.attempt."""
import sys

from games.site.views.game import attempt as _implementation

sys.modules[__name__] = _implementation
