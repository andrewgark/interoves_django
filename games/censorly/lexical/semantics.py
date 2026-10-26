"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.semantics."""
import sys

from games.puzzles.censorly.lexical import semantics as _implementation

sys.modules[__name__] = _implementation
