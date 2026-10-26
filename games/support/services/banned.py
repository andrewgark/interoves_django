"""Compatibility alias. Implementation lives in games.ops.support.people.banned."""
import sys

from games.ops.support.people import banned as _implementation

sys.modules[__name__] = _implementation
