"""Compatibility alias. Implementation lives in games.ops.social.instagram.models."""
import sys

from games.ops.social.instagram import models as _implementation

sys.modules[__name__] = _implementation
