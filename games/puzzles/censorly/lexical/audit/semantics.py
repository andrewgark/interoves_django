"""Compatibility alias. Implementation lives in games.puzzles.censorly.offline.audit.semantics."""
import sys

from games.puzzles.censorly.offline.audit import semantics as _implementation

sys.modules[__name__] = _implementation
