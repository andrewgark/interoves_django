"""Compatibility alias. Implementation lives in games.site.pages.pay.pages."""
import sys

from games.site.pages.pay import pages as _implementation

sys.modules[__name__] = _implementation
