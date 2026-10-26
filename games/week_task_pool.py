"""Compatibility alias. Implementation lives in games.sections.week_task_pool."""
import sys

from games.sections import week_task_pool as _implementation

sys.modules[__name__] = _implementation
