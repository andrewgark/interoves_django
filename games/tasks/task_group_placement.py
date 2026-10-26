"""Compatibility alias. Implementation lives in games.task_group.task_group_placement."""
import sys

from games.task_group import task_group_placement as _implementation

sys.modules[__name__] = _implementation
