"""Compatibility alias. Implementation lives in games.task_group.likes_meta."""
import sys

from games.task_group import likes_meta as _implementation

sys.modules[__name__] = _implementation
