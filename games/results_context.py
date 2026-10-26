"""Compatibility alias. Implementation lives in games.results.table.context."""
import sys

from games.results.table import context as _implementation

sys.modules[__name__] = _implementation
