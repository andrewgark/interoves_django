"""Compatibility alias. Implementation lives in games.ops.workers.lambda_entry."""
import sys

from games.ops.workers import lambda_entry as _implementation

sys.modules[__name__] = _implementation
