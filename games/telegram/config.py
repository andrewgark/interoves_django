"""Compatibility alias. Implementation lives in games.ops.telegram.transport.config."""
import sys

from games.ops.telegram.transport import config as _implementation

sys.modules[__name__] = _implementation
