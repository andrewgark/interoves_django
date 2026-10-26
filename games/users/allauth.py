"""Compatibility alias. Implementation lives in games.identity.allauth."""
import sys

from games.identity import allauth as _implementation

sys.modules[__name__] = _implementation
