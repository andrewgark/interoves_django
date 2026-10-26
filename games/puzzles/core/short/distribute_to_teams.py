"""Compatibility alias. Implementation lives in games.puzzles.markers.distribute_to_teams."""
import sys

from games.puzzles.markers import distribute_to_teams as _implementation

sys.modules[__name__] = _implementation
