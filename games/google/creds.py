"""Compatibility alias. Implementation lives in games.ops.support.google.creds."""
import sys

from games.ops.support.google import creds as _implementation

sys.modules[__name__] = _implementation
