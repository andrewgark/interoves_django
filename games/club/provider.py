"""Compatibility alias. Implementation lives in games.identity.provider."""
import sys

from games.identity import provider as _implementation

sys.modules[__name__] = _implementation
