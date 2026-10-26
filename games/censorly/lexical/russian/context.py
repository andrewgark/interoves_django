"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.context."""
import sys

from games.puzzles.censorly.lexical.russian import context as _implementation

sys.modules[__name__] = _implementation
