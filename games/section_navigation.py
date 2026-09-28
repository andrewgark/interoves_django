"""Compatibility alias for section navigation helpers."""

import sys

from games.sections import navigation as _implementation

sys.modules[__name__] = _implementation
