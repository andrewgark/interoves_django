"""Compatibility alias. Implementation lives in games.club.billing.gifts.payments."""
import sys

from games.club.billing.gifts import payments as _implementation

sys.modules[__name__] = _implementation
