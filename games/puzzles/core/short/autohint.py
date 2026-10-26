"""Compatibility alias. Implementation lives in games.puzzles.markers.autohint."""
import sys

from games.puzzles.markers import autohint as _implementation

sys.modules[__name__] = _implementation
