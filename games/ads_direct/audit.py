"""Compatibility alias. Implementation lives in games.ops.social.ads.audit."""
import sys

from games.ops.social.ads import audit as _implementation

sys.modules[__name__] = _implementation
