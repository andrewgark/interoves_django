"""Compatibility alias for daily difficulty calculations."""

import sys

from games.difficulty_domain import core as _implementation

sys.modules[__name__] = _implementation
