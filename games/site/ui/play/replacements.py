"""Compatibility alias. Implementation lives in games.site.pages.play.replacements."""
import sys

from games.site.pages.play import replacements as _implementation

sys.modules[__name__] = _implementation
