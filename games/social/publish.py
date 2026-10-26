"""Compatibility alias. Implementation lives in games.ops.social.publish."""
import sys

from games.ops.social import publish as _implementation

sys.modules[__name__] = _implementation
