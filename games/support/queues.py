"""Compatibility alias. Implementation lives in games.ops.support.http.queues."""
import sys

from games.ops.support.http import queues as _implementation

sys.modules[__name__] = _implementation
