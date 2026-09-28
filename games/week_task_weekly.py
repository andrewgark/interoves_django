"""Compatibility alias for the weekly task schedule adapter."""

import sys

from games.daily.catalog import week_task as _implementation

sys.modules[__name__] = _implementation
