"""Compatibility alias. Implementation lives in games.club.billing.yookassa.club."""
import sys

from games.club.billing.yookassa import club as _implementation

sys.modules[__name__] = _implementation
