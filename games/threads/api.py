"""Compatibility alias. Implementation lives in games.ops.social.threads.api."""
import sys

from games.ops.social.threads import api as _implementation

sys.modules[__name__] = _implementation
