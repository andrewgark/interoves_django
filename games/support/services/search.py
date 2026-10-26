"""Compatibility alias. Implementation lives in games.ops.support.people.search."""
import sys

from games.ops.support.people import search as _implementation

sys.modules[__name__] = _implementation
