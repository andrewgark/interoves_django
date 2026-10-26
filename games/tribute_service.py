"""Compatibility alias. Implementation lives in games.club.billing.tribute.service."""
import sys

from games.club.billing.tribute import service as _implementation

sys.modules[__name__] = _implementation
