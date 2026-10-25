"""Compatibility alias for daily result projection reconciliation."""

import sys

from games.daily.projection import cron as _implementation

sys.modules[__name__] = _implementation
