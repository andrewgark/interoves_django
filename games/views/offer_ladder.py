"""Compatibility alias. Implementation lives in games.site.views.offers.ladder."""
import sys

from games.site.views.offers import ladder as _implementation

sys.modules[__name__] = _implementation
