"""Compatibility alias. Implementation lives in games.site.ui.profile.feedback."""
import sys

from games.site.ui.profile import feedback as _implementation

sys.modules[__name__] = _implementation
