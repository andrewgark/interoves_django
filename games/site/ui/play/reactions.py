"""Compatibility alias. Implementation lives in games.site.pages.play.reactions."""
import sys

from games.site.pages.play import reactions as _implementation

sys.modules[__name__] = _implementation
