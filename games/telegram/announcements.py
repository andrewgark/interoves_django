"""Compatibility alias. Implementation lives in games.ops.telegram.channels.announcements."""
import sys

from games.ops.telegram.channels import announcements as _implementation

sys.modules[__name__] = _implementation
