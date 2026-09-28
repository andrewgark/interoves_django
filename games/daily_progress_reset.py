"""Compatibility alias for daily progress reset operations."""

import sys

from games.daily import progress_reset as _implementation

sys.modules[__name__] = _implementation
