"""Compatibility alias. Implementation lives in games.club.merge.events."""
import sys

from games.club.merge import events as _implementation

sys.modules[__name__] = _implementation
