"""Compatibility alias. Implementation lives in games.tasks.actor_state."""
import sys

from games.tasks import actor_state as _implementation

sys.modules[__name__] = _implementation
