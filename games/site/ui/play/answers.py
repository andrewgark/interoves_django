"""Compatibility alias. Implementation lives in games.site.pages.play.answers."""
import sys

from games.site.pages.play import answers as _implementation

sys.modules[__name__] = _implementation
