"""Compatibility alias. Implementation lives in games.site.views.offers.salad."""
import sys

from games.site.views.offers import salad as _implementation

sys.modules[__name__] = _implementation
