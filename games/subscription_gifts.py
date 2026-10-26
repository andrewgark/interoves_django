"""Compatibility alias. Implementation lives in games.club.billing.gifts.offers."""
import sys

from games.club.billing.gifts import offers as _implementation

sys.modules[__name__] = _implementation
