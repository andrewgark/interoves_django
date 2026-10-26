"""Compatibility alias. Implementation lives in games.ops.support.observatory.serializers."""
import sys

from games.ops.support.observatory import serializers as _implementation

sys.modules[__name__] = _implementation
