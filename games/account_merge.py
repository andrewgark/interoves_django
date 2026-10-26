"""Compatibility alias. Implementation lives in games.club.merge.account."""
import sys

from games.club.merge import account as _implementation

sys.modules[__name__] = _implementation
