"""Compatibility alias. Implementation lives in games.console.http.constants."""
import sys

from games.console.http import constants as _implementation

sys.modules[__name__] = _implementation
