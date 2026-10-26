"""Compatibility alias. Implementation lives in games.ops.telegram.images.censorly."""
import sys

from games.ops.telegram.images import censorly as _implementation

sys.modules[__name__] = _implementation
