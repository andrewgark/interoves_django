"""Compatibility alias. Implementation lives in games.ops.social.ads.config."""
import sys

from games.ops.social.ads import config as _implementation

sys.modules[__name__] = _implementation
