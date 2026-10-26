"""Compatibility alias. Implementation lives in games.tasks.titles."""
import sys

from games.tasks import titles as _implementation

sys.modules[__name__] = _implementation
