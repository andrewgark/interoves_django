"""Compatibility alias. Implementation lives in games.task_group.wall_max_points."""
import sys

from games.task_group import wall_max_points as _implementation

sys.modules[__name__] = _implementation
