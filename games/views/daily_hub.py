"""Compatibility alias. Implementation lives in games.site.views.daily.hub."""
import sys

from games.site.views.daily import hub as _implementation

sys.modules[__name__] = _implementation
