"""Compatibility alias. Implementation lives in games.ops.telegram.images.announce."""
import sys

from games.ops.telegram.images import announce as _implementation

sys.modules[__name__] = _implementation
