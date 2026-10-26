"""Compatibility alias. Implementation lives in games.results.util."""
import sys

from games.results import util as _implementation

sys.modules[__name__] = _implementation
