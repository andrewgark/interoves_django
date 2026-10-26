"""Compatibility alias. Implementation lives in games.daily.page.section."""
import sys

from games.daily.page import section as _implementation

sys.modules[__name__] = _implementation
