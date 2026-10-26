"""Compatibility alias. Implementation lives in games.club.billing.tribute.checks."""
import sys

from games.club.billing.tribute import checks as _implementation

sys.modules[__name__] = _implementation
