"""Compatibility alias. Implementation lives in games.task_group.actor_state."""
import sys

from games.task_group import actor_state as _implementation

sys.modules[__name__] = _implementation
