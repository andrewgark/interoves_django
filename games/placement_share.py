"""Compatibility alias. Implementation lives in games.sections.placement_share."""
import sys

from games.sections import placement_share as _implementation

sys.modules[__name__] = _implementation
