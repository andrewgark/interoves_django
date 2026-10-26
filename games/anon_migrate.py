"""Compatibility alias. Implementation lives in games.club.merge.anon."""
import sys

from games.club.merge import anon as _implementation

sys.modules[__name__] = _implementation
