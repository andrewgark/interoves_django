"""Compatibility alias. Implementation lives in games.ops.health.checks."""
import sys

from games.ops.health import checks as _implementation

sys.modules[__name__] = _implementation
