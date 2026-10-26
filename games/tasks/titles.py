"""Compatibility alias. Implementation lives in games.task_group.titles."""
import sys

from games.task_group import titles as _implementation

sys.modules[__name__] = _implementation
