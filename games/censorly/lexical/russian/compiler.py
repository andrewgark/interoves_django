"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.russian.compiler."""
import sys

from games.puzzles.censorly.lexical.russian import compiler as _implementation

sys.modules[__name__] = _implementation
