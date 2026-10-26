"""Compatibility alias. Implementation lives in games.console.people.search."""
import sys

from games.console.people import search as _implementation

sys.modules[__name__] = _implementation
