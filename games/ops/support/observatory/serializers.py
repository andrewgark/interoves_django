"""Compatibility alias. Implementation lives in games.console.observatory.serializers."""
import sys

from games.console.observatory import serializers as _implementation

sys.modules[__name__] = _implementation
