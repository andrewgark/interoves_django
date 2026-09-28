"""Compatibility alias for daily difficulty refresh operations."""

import sys

from games.difficulty_domain import refresh as _implementation

sys.modules[__name__] = _implementation
