"""Compatibility alias. Implementation lives in games.identity.merge.account_queue."""
import sys

from games.identity.merge import account_queue as _implementation

sys.modules[__name__] = _implementation
