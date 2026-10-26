"""Compatibility alias. Implementation lives in games.site.session.replay_planner."""
import sys

from games.site.session import replay_planner as _implementation

sys.modules[__name__] = _implementation
