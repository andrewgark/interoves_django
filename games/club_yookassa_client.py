"""Compatibility alias. Implementation lives in games.club.billing.yookassa.client."""
import sys

from games.club.billing.yookassa import client as _implementation

sys.modules[__name__] = _implementation
