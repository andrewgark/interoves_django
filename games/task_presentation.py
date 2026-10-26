"""Compatibility alias. Implementation lives in games.tasks.presentation."""
import sys

from games.tasks import presentation as _implementation

sys.modules[__name__] = _implementation
