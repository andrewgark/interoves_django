"""Compatibility alias. Implementation lives in games.ops.support.observatory.explanations."""
import sys

from games.ops.support.observatory import explanations as _implementation

sys.modules[__name__] = _implementation
