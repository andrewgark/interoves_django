"""Compatibility alias. Implementation lives in games.ops.telegram.transport.webhook."""
import sys

from games.ops.telegram.transport import webhook as _implementation

sys.modules[__name__] = _implementation
