"""Compatibility alias. Implementation lives in games.ops.social.instagram.api."""
import sys

from games.ops.social.instagram import api as _implementation

sys.modules[__name__] = _implementation
