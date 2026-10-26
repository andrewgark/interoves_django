"""Compatibility alias. Implementation lives in games.console.people.pending."""
import sys

from games.console.people import pending as _implementation

sys.modules[__name__] = _implementation
