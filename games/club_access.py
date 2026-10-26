"""Compatibility alias. Implementation lives in games.club.access.archive."""
import sys

from games.club.access import archive as _implementation

sys.modules[__name__] = _implementation
