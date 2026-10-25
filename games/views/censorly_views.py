"""Compatibility alias. Implementation lives in games.site.views.daily.censorly."""
import sys

from games.site.views.daily import censorly as _implementation

sys.modules[__name__] = _implementation
