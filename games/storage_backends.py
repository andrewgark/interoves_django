"""Historical migration import path (``0232``). Runtime: ``games.ops.health.storage``."""
import sys

from games.ops.health import storage as _implementation

sys.modules[__name__] = _implementation
