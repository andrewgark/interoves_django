"""Compatibility alias. Implementation lives in games.task_group.navigation."""
import sys

from games.task_group import navigation as _implementation

sys.modules[__name__] = _implementation
