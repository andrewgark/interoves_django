"""Compatibility alias. Implementation lives in games.console.google.creds."""
import sys

from games.console.google import creds as _implementation

sys.modules[__name__] = _implementation
