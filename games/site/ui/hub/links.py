"""Compatibility alias. Implementation lives in games.site.pages.hub.links."""
import sys

from games.site.pages.hub import links as _implementation

sys.modules[__name__] = _implementation
