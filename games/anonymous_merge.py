"""Compatibility alias. Implementation lives in games.club.merge.anonymous."""
import sys

from games.club.merge import anonymous as _implementation

sys.modules[__name__] = _implementation
