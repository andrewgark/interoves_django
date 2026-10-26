"""Compatibility alias. Implementation lives in games.ops.telegram.images.errors."""
import sys

from games.ops.telegram.images import errors as _implementation

sys.modules[__name__] = _implementation
