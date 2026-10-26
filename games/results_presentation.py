"""Compatibility alias. Implementation lives in games.results.table.presentation."""
import sys

from games.results.table import presentation as _implementation

sys.modules[__name__] = _implementation
