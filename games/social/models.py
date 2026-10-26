"""Compatibility alias. Implementation lives in games.ops.social.models."""
import sys

from games.ops.social import models as _implementation

sys.modules[__name__] = _implementation
