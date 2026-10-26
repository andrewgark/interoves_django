"""Compatibility alias. Implementation lives in games.ops.social.ads.client."""
import sys

from games.ops.social.ads import client as _implementation

sys.modules[__name__] = _implementation
