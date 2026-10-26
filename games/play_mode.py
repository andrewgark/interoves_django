"""Compatibility alias. Implementation lives in games.site.session.play_mode."""
import sys

from games.site.session import play_mode as _implementation

sys.modules[__name__] = _implementation
