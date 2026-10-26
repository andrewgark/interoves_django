"""Compatibility alias. Implementation lives in games.ops.social.instagram.refresh."""
import sys

from games.ops.social.instagram import refresh as _implementation

sys.modules[__name__] = _implementation
