"""Compatibility alias. Implementation lives in games.club.offers.censorly."""
import sys

from games.club.offers import censorly as _implementation

sys.modules[__name__] = _implementation
