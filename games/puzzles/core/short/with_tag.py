"""Compatibility alias. Implementation lives in games.puzzles.markers.with_tag."""
import sys

from games.puzzles.markers import with_tag as _implementation

sys.modules[__name__] = _implementation
