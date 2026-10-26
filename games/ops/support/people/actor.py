"""Compatibility alias. Implementation lives in games.console.people.actor."""
import sys

from games.console.people import actor as _implementation

sys.modules[__name__] = _implementation
