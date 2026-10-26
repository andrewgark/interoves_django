"""Compatibility alias. Implementation lives in games.club.billing.tickets."""
import sys

from games.club.billing import tickets as _implementation

sys.modules[__name__] = _implementation
