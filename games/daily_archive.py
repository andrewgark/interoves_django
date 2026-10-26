"""Compatibility alias. Implementation lives in games.daily.page.archive."""
import sys

from games.daily.page import archive as _implementation

sys.modules[__name__] = _implementation
