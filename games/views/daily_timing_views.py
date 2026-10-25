"""Compatibility alias. Implementation lives in games.site.views.daily.timing."""
import sys

from games.site.views.daily import timing as _implementation

sys.modules[__name__] = _implementation
