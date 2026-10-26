"""Compatibility alias. Implementation lives in games.ops.telegram.images.ladder."""
import sys

from games.ops.telegram.images import ladder as _implementation

sys.modules[__name__] = _implementation
