"""Compatibility alias. Implementation lives in games.tasks.navigation."""
import sys

from games.tasks import navigation as _implementation

sys.modules[__name__] = _implementation
