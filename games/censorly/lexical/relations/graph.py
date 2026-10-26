"""Compatibility alias. Implementation lives in games.puzzles.censorly.lexical.graph.graph."""
import sys

from games.puzzles.censorly.lexical.graph import graph as _implementation

sys.modules[__name__] = _implementation
