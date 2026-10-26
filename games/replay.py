"""Compatibility alias. Implementation lives in games.site.session.replay."""
import sys

from games.site.session import replay as _implementation

sys.modules[__name__] = _implementation
