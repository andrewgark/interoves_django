"""Compatibility alias. Implementation lives in games.tasks.progress."""
import sys

from games.tasks import progress as _implementation

sys.modules[__name__] = _implementation
