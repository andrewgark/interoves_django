"""Compatibility alias. Implementation lives in games.ops.social.ads.money."""
import sys

from games.ops.social.ads import money as _implementation

sys.modules[__name__] = _implementation
