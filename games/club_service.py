"""Compatibility alias. Implementation lives in games.club.access.service."""
import sys

from games.club.access import service as _implementation

sys.modules[__name__] = _implementation
