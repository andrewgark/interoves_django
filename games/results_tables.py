"""Compatibility alias. Implementation lives in games.results.table.tables."""
import sys

from games.results.table import tables as _implementation

sys.modules[__name__] = _implementation
