"""Compatibility alias. Implementation lives in games.console.observatory.selectors."""
import sys

from games.console.observatory import selectors as _implementation

sys.modules[__name__] = _implementation
