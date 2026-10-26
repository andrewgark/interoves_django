"""Compatibility alias. Implementation lives in games.site.pages.game.progress."""
import sys

from games.site.pages.game import progress as _implementation

sys.modules[__name__] = _implementation
