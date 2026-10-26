"""Compatibility alias. Implementation lives in games.identity.merge.anon_queue."""
import sys

from games.identity.merge import anon_queue as _implementation

sys.modules[__name__] = _implementation
