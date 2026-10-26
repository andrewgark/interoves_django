"""Compatibility alias. Implementation lives in games.club.offers.salad."""
import sys

from games.club.offers import salad as _implementation

sys.modules[__name__] = _implementation
