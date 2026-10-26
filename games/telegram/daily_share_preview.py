"""Compatibility alias. Implementation lives in games.ops.telegram.images.share."""
import sys

from games.ops.telegram.images import share as _implementation

sys.modules[__name__] = _implementation
