"""Compatibility alias. Implementation lives in games.identity.merge.events."""
import sys

from games.identity.merge import events as _implementation

sys.modules[__name__] = _implementation
