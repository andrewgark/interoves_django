"""Compatibility alias. Implementation lives in games.club.billing.donations."""
import sys

from games.club.billing import donations as _implementation

sys.modules[__name__] = _implementation
