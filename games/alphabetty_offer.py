"""Compatibility alias. Implementation lives in games.club.offers.alphabetty."""
import sys

from games.club.offers import alphabetty as _implementation

sys.modules[__name__] = _implementation
