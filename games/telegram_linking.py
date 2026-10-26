"""Compatibility alias. Implementation lives in games.club.link."""
import sys

from games.club import link as _implementation

sys.modules[__name__] = _implementation
