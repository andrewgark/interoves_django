"""Compatibility alias. Implementation lives in games.site.pages.profile.feedback."""
import sys

from games.site.pages.profile import feedback as _implementation

sys.modules[__name__] = _implementation
