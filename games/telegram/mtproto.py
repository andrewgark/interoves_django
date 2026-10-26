"""Compatibility alias. Implementation lives in games.ops.telegram.transport.mtproto."""
import sys

from games.ops.telegram.transport import mtproto as _implementation

sys.modules[__name__] = _implementation
