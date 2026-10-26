"""Compatibility alias. Implementation lives in games.ops.telegram.models."""
import sys

from games.ops.telegram import models as _implementation

sys.modules[__name__] = _implementation
