"""Compatibility alias. Implementation lives in games.ops.telegram.channels.review."""
import sys

from games.ops.telegram.channels import review as _implementation

sys.modules[__name__] = _implementation
