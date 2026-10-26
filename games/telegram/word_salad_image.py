"""Compatibility alias. Implementation lives in games.ops.telegram.images.salad."""
import sys

from games.ops.telegram.images import salad as _implementation

sys.modules[__name__] = _implementation
