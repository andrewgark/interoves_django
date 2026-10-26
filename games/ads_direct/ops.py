"""Compatibility alias. Implementation lives in games.ops.social.ads.ops."""
import sys

from games.ops.social.ads import ops as _implementation

sys.modules[__name__] = _implementation
