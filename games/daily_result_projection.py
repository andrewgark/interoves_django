"""Compatibility alias for the daily result projection implementation."""

import sys

from games.daily import projection as _implementation

sys.modules[__name__] = _implementation
