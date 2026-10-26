"""Compatibility alias. Implementation lives in games.site.pages.profile.form."""
import sys

from games.site.pages.profile import form as _implementation

sys.modules[__name__] = _implementation
