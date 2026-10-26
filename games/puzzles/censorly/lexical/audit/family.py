"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.audit.family."""
import sys

from games.puzzles.censorly.offline.audit import family as _implementation

sys.modules[__name__] = _implementation
