"""Compatibility alias. Implementation lives in games.site.views.account.ticket."""
import sys

from games.site.views.account import ticket as _implementation

sys.modules[__name__] = _implementation
