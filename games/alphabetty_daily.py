"""Compatibility alias for the Alphabetty daily schedule adapter."""

import sys

from games.daily.catalog import alphabetty as _implementation

sys.modules[__name__] = _implementation
