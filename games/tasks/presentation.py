"""Compatibility alias. Implementation lives in games.task_group.presentation."""
import sys

from games.task_group import presentation as _implementation

sys.modules[__name__] = _implementation
