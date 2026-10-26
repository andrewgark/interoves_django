"""Compatibility alias. Implementation lives in games.site.session.replay_navigation."""
import sys

from games.site.session import replay_navigation as _implementation

sys.modules[__name__] = _implementation
