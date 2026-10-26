"""Compatibility alias. Implementation lives in games.site.forms."""
import sys

from games.site import forms as _implementation

sys.modules[__name__] = _implementation
