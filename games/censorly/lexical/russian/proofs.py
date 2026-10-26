"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.proofs."""
import sys

from games.puzzles.censorly.lexical.russian import proofs as _implementation

sys.modules[__name__] = _implementation
