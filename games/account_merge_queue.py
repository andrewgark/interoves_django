"""Compatibility alias. Implementation lives in games.club.merge.queue."""
import sys

from games.club.merge import queue as _implementation

sys.modules[__name__] = _implementation
