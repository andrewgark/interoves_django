"""Compatibility alias. Implementation lives in games.site.views.offers.censorly."""
import sys

from games.site.views.offers import censorly as _implementation

sys.modules[__name__] = _implementation
