"""Compatibility alias. Implementation lives in games.task_group.proportions_task_data."""
import sys

from games.task_group import proportions_task_data as _implementation

sys.modules[__name__] = _implementation
