"""Compatibility alias for daily progress reset operations."""

import sys

from games.daily.timing import reset as _implementation

sys.modules[__name__] = _implementation
