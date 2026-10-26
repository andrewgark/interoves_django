"""Compatibility alias. Implementation lives in games.ops.support.http.constants."""
import sys

from games.ops.support.http import constants as _implementation

sys.modules[__name__] = _implementation
