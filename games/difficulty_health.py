"""Compatibility alias for the daily difficulty health check."""

import sys

from games.daily.difficulty import health as _implementation

sys.modules[__name__] = _implementation
