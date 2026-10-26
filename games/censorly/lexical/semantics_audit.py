"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.audit.semantics."""
import sys

from games.puzzles.censorly.lexical.audit import semantics as _implementation

sys.modules[__name__] = _implementation
