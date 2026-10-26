"""Compatibility alias. Implementation lives in games.club.offers.ladder."""
import sys

from games.club.offers import ladder as _implementation

sys.modules[__name__] = _implementation
