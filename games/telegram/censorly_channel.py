"""Compatibility alias. Implementation lives in games.ops.telegram.channels.censorly."""
import sys

from games.ops.telegram.channels import censorly as _implementation

sys.modules[__name__] = _implementation
