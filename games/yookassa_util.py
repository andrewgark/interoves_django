"""Compatibility alias. Implementation lives in games.club.billing.yookassa.util."""
import sys

from games.club.billing.yookassa import util as _implementation

sys.modules[__name__] = _implementation
