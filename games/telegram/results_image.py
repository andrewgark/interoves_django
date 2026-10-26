"""Compatibility alias. Implementation lives in games.ops.telegram.images.results."""
import sys

from games.ops.telegram.images import results as _implementation

sys.modules[__name__] = _implementation
