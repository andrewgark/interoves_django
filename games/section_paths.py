"""Compatibility alias for canonical section URL paths."""

import sys

from games.sections import paths as _implementation

sys.modules[__name__] = _implementation
