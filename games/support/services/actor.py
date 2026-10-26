"""Compatibility alias. Implementation lives in games.ops.support.people.actor."""
import sys

from games.ops.support.people import actor as _implementation

sys.modules[__name__] = _implementation
