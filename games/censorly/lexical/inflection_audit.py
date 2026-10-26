"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.audit.inflection."""
import sys

from games.puzzles.censorly.lexical.audit import inflection as _implementation

sys.modules[__name__] = _implementation
