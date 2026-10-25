"""Compatibility alias. Implementation lives in games.site.views.offers.alphabetty."""
import sys

from games.site.views.offers import alphabetty as _implementation

sys.modules[__name__] = _implementation
