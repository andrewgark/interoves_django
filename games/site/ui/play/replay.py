"""Compatibility alias. Implementation lives in games.site.pages.play.replay."""
import sys

from games.site.pages.play import replay as _implementation

sys.modules[__name__] = _implementation
