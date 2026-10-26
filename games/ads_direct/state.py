"""Compatibility alias. Implementation lives in games.ops.social.ads.state."""
import sys

from games.ops.social.ads import state as _implementation

sys.modules[__name__] = _implementation
