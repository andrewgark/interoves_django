"""Compatibility alias for daily difficulty refresh operations."""

import sys

from games.daily.difficulty import refresh as _implementation

sys.modules[__name__] = _implementation
