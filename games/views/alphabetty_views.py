"""Compatibility alias. Implementation lives in games.site.views.daily.alphabetty."""
import sys

from games.site.views.daily import alphabetty as _implementation

sys.modules[__name__] = _implementation
