"""Compatibility alias. Implementation lives in games.ops.telegram.channels.scheduling."""
import sys

from games.ops.telegram.channels import scheduling as _implementation

sys.modules[__name__] = _implementation
