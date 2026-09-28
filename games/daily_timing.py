"""Compatibility alias for the daily timing implementation.

The module alias keeps legacy imports and module-level test/integration hooks
working while the implementation lives under ``games.daily``.
"""

import sys

from games.daily import timing as _implementation

sys.modules[__name__] = _implementation
