"""Compatibility alias. Implementation lives in games.task_group.task_group_ui."""
import sys

from games.task_group import task_group_ui as _implementation

sys.modules[__name__] = _implementation
