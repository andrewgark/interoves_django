"""Compatibility alias. Implementation lives in games.ops.telegram.transport.provider."""
import sys

from games.ops.telegram.transport import provider as _implementation

sys.modules[__name__] = _implementation
