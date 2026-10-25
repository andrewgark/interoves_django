"""Compatibility alias. Implementation lives in games.site.views.account.subscription."""
import sys

from games.site.views.account import subscription as _implementation

sys.modules[__name__] = _implementation
