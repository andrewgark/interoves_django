"""Compatibility alias. Implementation lives in games.club.offers.profiles."""
import sys

from games.club.offers import profiles as _implementation

sys.modules[__name__] = _implementation
