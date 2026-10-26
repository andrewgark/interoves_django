"""Compatibility alias. Implementation lives in games.console.http.queues."""
import sys

from games.console.http import queues as _implementation

sys.modules[__name__] = _implementation
