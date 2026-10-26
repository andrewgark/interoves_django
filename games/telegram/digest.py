"""Compatibility alias. Implementation lives in games.ops.telegram.channels.digest."""
import sys

from games.ops.telegram.channels import digest as _implementation

sys.modules[__name__] = _implementation
