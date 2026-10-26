"""Compatibility alias. Implementation lives in games.task_group.progress."""
import sys

from games.task_group import progress as _implementation

sys.modules[__name__] = _implementation
