"""Compatibility alias. Implementation lives in games.task_group.replacements_state."""
import sys

from games.task_group import replacements_state as _implementation

sys.modules[__name__] = _implementation
