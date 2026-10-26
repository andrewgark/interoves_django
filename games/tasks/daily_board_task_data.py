"""Compatibility alias. Implementation lives in games.task_group.daily_board_task_data."""
import sys

from games.task_group import daily_board_task_data as _implementation

sys.modules[__name__] = _implementation
