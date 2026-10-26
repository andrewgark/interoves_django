"""Compatibility alias. Implementation lives in games.site.pages.hub.cards."""
import sys

from games.site.pages.hub import cards as _implementation

sys.modules[__name__] = _implementation
