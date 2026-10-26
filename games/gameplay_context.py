"""Compatibility alias. Implementation lives in games.site.session.gameplay_context."""
import sys

from games.site.session import gameplay_context as _implementation

sys.modules[__name__] = _implementation
