"""Compatibility alias. Implementation lives in games.ops.support.people.pending."""
import sys

from games.ops.support.people import pending as _implementation

sys.modules[__name__] = _implementation
