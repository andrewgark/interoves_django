"""Compatibility alias. Implementation lives in games.ops.telegram.channels.salad."""
import sys

from games.ops.telegram.channels import salad as _implementation

sys.modules[__name__] = _implementation
