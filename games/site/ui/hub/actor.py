"""Compatibility alias. Implementation lives in games.site.pages.hub.actor."""
import sys

from games.site.pages.hub import actor as _implementation

sys.modules[__name__] = _implementation
