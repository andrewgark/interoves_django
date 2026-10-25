"""Compatibility alias. Implementation lives in games.site.views.account.team."""
import sys

from games.site.views.account import team as _implementation

sys.modules[__name__] = _implementation
