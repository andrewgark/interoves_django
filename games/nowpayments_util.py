"""Compatibility alias. Implementation lives in games.club.billing.crypto."""
import sys

from games.club.billing import crypto as _implementation

sys.modules[__name__] = _implementation
