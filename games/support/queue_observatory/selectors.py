"""Compatibility alias. Implementation lives in games.ops.support.observatory.selectors."""
import sys

from games.ops.support.observatory import selectors as _implementation

sys.modules[__name__] = _implementation
