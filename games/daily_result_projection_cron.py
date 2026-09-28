"""Compatibility alias for daily result projection reconciliation."""

import sys

from games.daily import projection_cron as _implementation

sys.modules[__name__] = _implementation
