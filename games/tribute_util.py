"""Compatibility alias. Implementation lives in games.club.billing.tribute.util."""
import sys

from games.club.billing.tribute import util as _implementation

sys.modules[__name__] = _implementation
