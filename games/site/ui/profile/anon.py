"""Compatibility alias. Implementation lives in games.site.pages.profile.anon."""
import sys

from games.site.pages.profile import anon as _implementation

sys.modules[__name__] = _implementation
