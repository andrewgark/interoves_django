"""Compatibility alias. Implementation lives in games.identity.merge.account."""
import sys

from games.identity.merge import account as _implementation

sys.modules[__name__] = _implementation
