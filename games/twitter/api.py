"""Compatibility alias. Implementation lives in games.ops.social.twitter.api."""
import sys

from games.ops.social.twitter import api as _implementation

sys.modules[__name__] = _implementation
