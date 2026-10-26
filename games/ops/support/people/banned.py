"""Compatibility alias. Implementation lives in games.console.people.banned."""
import sys

from games.console.people import banned as _implementation

sys.modules[__name__] = _implementation
