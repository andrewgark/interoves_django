"""Compatibility alias. Implementation lives in games.site.views.account.registration."""
import sys

from games.site.views.account import registration as _implementation

sys.modules[__name__] = _implementation
