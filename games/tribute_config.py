"""Compatibility alias. Implementation lives in games.club.billing.tribute.config."""
import sys

from games.club.billing.tribute import config as _implementation

sys.modules[__name__] = _implementation
