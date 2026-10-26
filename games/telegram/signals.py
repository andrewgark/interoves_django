"""Compatibility alias. Implementation lives in games.ops.telegram.signals."""
import sys

from games.ops.telegram import signals as _implementation

sys.modules[__name__] = _implementation
