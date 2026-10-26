"""Compatibility alias. Implementation lives in games.results.table.access."""
import sys

from games.results.table import access as _implementation

sys.modules[__name__] = _implementation
