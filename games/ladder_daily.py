"""Compatibility alias for the Ladder daily schedule adapter."""

import sys

from games.daily.catalog import ladder as _implementation

sys.modules[__name__] = _implementation
