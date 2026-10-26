"""Compatibility alias. Implementation lives in games.club.provider."""
import sys

from games.club import provider as _implementation

sys.modules[__name__] = _implementation
