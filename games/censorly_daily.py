"""Compatibility alias. Implementation lives in games.daily.catalog.censorly."""
import sys

from games.daily.catalog import censorly as _implementation

sys.modules[__name__] = _implementation
