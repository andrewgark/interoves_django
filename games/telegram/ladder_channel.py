"""Compatibility alias. Implementation lives in games.ops.telegram.channels.ladder."""
import sys

from games.ops.telegram.channels import ladder as _implementation

sys.modules[__name__] = _implementation
