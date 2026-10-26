"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.audit.family."""
import sys

from games.puzzles.censorly.lexical.audit import family as _implementation

sys.modules[__name__] = _implementation
